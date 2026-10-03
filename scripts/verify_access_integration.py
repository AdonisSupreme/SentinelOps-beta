"""Real PostgreSQL + existing session authentication tests on an isolated local cluster.

Uses only .tmp/sentinel-access-pg on port 55439 and refuses any other cluster.
No running SentinelOps database, scheduler, or business integrations are used.
"""
import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'SentinelOps-beta'))
BASE_DSN = 'postgresql://sentinel_access_test@127.0.0.1:55439/postgres'
import psycopg

with psycopg.connect(BASE_DSN,autocommit=True) as check:
    directory=check.execute('SHOW data_directory').fetchone()[0]
    assert Path(directory).resolve()==(ROOT/'.tmp/sentinel-access-pg').resolve(), 'Refusing non-test database'

schema='access_test_'+uuid4().hex
dsn=BASE_DSN+'?options='+quote('-c search_path='+schema)
os.environ.update(DATABASE_URL=dsn, SECRET_KEY='isolated-access-integration-test-secret', CENTRAL_AUTH_URL='http://127.0.0.1:1')

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
import jwt
from app.access.router import router
from app.users.router import router as users_router
from app.access.middleware import ModuleAccessMiddleware
from app.auth.service import get_current_user
from app.auth.router import router as auth_router
from unittest.mock import patch
from app.db.database import get_connection
from app.access.policy import resolve_access
logging.disable(logging.CRITICAL)

with psycopg.connect(BASE_DSN,autocommit=True) as control:
    control.execute(psycopg.sql.SQL('CREATE SCHEMA {}').format(psycopg.sql.Identifier(schema)))
try:
    with psycopg.connect(dsn,autocommit=True) as conn:
        conn.execute('''
          CREATE TABLE sections(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),section_name text NOT NULL,created_at timestamptz DEFAULT now(),manager_id uuid);
          CREATE UNIQUE INDEX sections_name ON sections(lower(section_name));
          CREATE TABLE department(id serial PRIMARY KEY,department_name text);
          CREATE TABLE users(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),username text UNIQUE,email text,first_name text,last_name text,
            password_hash text,department_id integer,section_id uuid REFERENCES sections(id),is_active boolean DEFAULT true,created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now());
          CREATE TABLE roles(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),name text UNIQUE);
          CREATE TABLE user_roles(user_id uuid REFERENCES users(id),role_id uuid REFERENCES roles(id),assigned_at timestamptz,PRIMARY KEY(user_id,role_id));
          CREATE TABLE auth_sessions(id uuid PRIMARY KEY,user_id uuid,revoked_at timestamptz,expires_at timestamptz);
          CREATE TABLE ops_events(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),event_type text,entity_type text,entity_id uuid,payload jsonb,created_at timestamptz DEFAULT now());
          CREATE TABLE notifications(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),user_id uuid,role_id uuid,title text,message text,related_entity text,related_id uuid,is_read boolean DEFAULT false,created_at timestamptz DEFAULT now());
        ''')
        section_a='7bd4144d-68d8-4ac3-897d-245941612daf'
        section_b=str(uuid4())
        conn.execute('INSERT INTO sections(id,section_name) VALUES (%s,\'Section A\'),(%s,\'Section B\')',(section_a,section_b))
        identities={}
        for name,role,section in [('admin','admin',section_b),('manager','manager',section_b),('operator','user',section_a),('legacy','user',None)]:
            uid,sid=str(uuid4()),str(uuid4())
            conn.execute('INSERT INTO roles(name) VALUES (%s) ON CONFLICT DO NOTHING',(role,))
            conn.execute('INSERT INTO users(id,username,email,first_name,last_name,section_id) VALUES (%s,%s,%s,\'Test\',\'User\',%s)',(uid,name,name+'@example.com',section))
            conn.execute('INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE name=%s',(uid,role))
            conn.execute("INSERT INTO auth_sessions VALUES (%s,%s,NULL,now()+interval '1 hour')",(sid,uid))
            token=jwt.encode({'sub':uid,'sid':sid,'exp':datetime.now(timezone.utc)+timedelta(hours=1)},os.environ['SECRET_KEY'],algorithm='HS256')
            identities[name]={'id':uid,'role':role,'headers':{'Authorization':'Bearer '+token}}
        migration=(ROOT/'SentinelOps-beta/app/db/migrations/2026_09_section_module_access.sql').read_text(encoding='utf-8')
        conn.execute(migration)
        assert conn.execute('SELECT section_id FROM users WHERE id=%s',(identities['legacy']['id'],)).fetchone()[0]
        conn.execute('DELETE FROM section_modules WHERE section_id=%s AND module_id=(SELECT id FROM access_modules WHERE module_key=\'reports.crb\')',(section_a,))
        conn.execute(migration)
        assert 'reports.crb' not in resolve_access(identities['operator'],conn)['modules']
        assert 'trustlink.daily_extraction' in resolve_access(identities['manager'],conn)['modules']
        assert 'reports.hovering' not in resolve_access(identities['manager'],conn)['modules']
        assert len(resolve_access(identities['admin'],conn)['modules'])==len(conn.execute('SELECT * FROM access_modules').fetchall())

    app=FastAPI()
    app.add_middleware(ModuleAccessMiddleware,authenticate=get_current_user,connect=get_connection)
    app.include_router(router,prefix='/api/v1')
    app.include_router(users_router,prefix='/api/v1')
    app.include_router(auth_router,prefix='/api/v1')
    @app.get('/api/v1/nexus/reports/crb/overview')
    def operational_read(user=Depends(get_current_user)):
        assert 'reports.crb' in user['access']['modules']
        return {'data':'CRB test data'}
    with TestClient(app,raise_server_exceptions=False) as client:
        admin=identities['admin']['headers']
        manager=identities['manager']['headers']
        with patch('app.auth.service.get_connection', wraps=get_connection) as connections:
            profile = client.get('/api/v1/auth/me', headers=admin)
            assert profile.status_code == 200, profile.text
            assert 'reports.crb' in profile.json()['access']['modules']
            assert connections.call_count == 1
        with patch('app.auth.service.get_connection', wraps=get_connection) as connections:
            assert client.get('/api/v1/nexus/reports/crb/overview', headers=admin).status_code == 200
            assert connections.call_count == 1, 'Middleware and dependency must reuse one authentication connection'
        assert client.get('/api/v1/me/access').status_code==401
        assert client.get('/api/v1/admin/access',headers=manager).status_code==403
        catalog=client.get('/api/v1/admin/access',headers=admin).json()
        crb=next(m['id'] for m in catalog['modules'] if m['module_key']=='reports.crb')
        assert client.put(f'/api/v1/admin/sections/{section_b}/modules',headers=manager,json={'module_ids':[crb]}).status_code==403
        assert client.put(f'/api/v1/admin/modules/{crb}/sections',headers=admin,json={'section_ids':[section_a,section_b,section_b]}).status_code==200
        assert client.get('/api/v1/nexus/reports/crb/overview',headers=manager).status_code==200
        assert client.put(f'/api/v1/admin/sections/{section_b}/modules',headers=admin,json={'module_ids':[]}).status_code==200
        assert client.get('/api/v1/nexus/reports/crb/overview',headers=manager).status_code==403
        assert client.get('/api/v1/nexus/reports/crb/overview',headers=identities['operator']['headers']).status_code==200
        assert client.put(f'/api/v1/admin/sections/{section_b}/modules',headers=admin,json={'module_ids':[str(uuid4())]}).status_code==422
        assert client.get('/api/v1/me/access',headers=manager).json()['modules']==[]
        response=client.patch('/api/v1/users/'+identities['manager']['id'],headers=admin,json={'section_id':section_a,'role':'user'})
        assert response.status_code==200, response.text
        effective=client.get('/api/v1/me/access',headers=manager).json()
        assert effective['role']=='USER' and 'reports.crb' in effective['modules']
        assert client.patch(f'/api/v1/admin/modules/{crb}',headers=admin,json={'is_active':False}).status_code==200
        assert 'reports.crb' not in client.get('/api/v1/me/access',headers=admin).json()['modules']
        assert client.patch(f'/api/v1/admin/modules/{crb}',headers=admin,json={'is_active':True}).status_code==200
        assert client.patch(f'/api/v1/admin/sections/{section_a}',headers=admin,json={'name':'Section A','is_active':False}).status_code==200
        assert client.get('/api/v1/me/access',headers=manager).json()['modules']==[]
        assert client.patch('/api/v1/users/'+identities['manager']['id'],headers=admin,json={'section_id':None}).status_code==200
        assert client.get('/api/v1/me/access',headers=manager).json()['section'] is None
        events=client.get('/api/v1/admin/access/audit',headers=admin).json()
        assert {'MODULE_ASSIGNED','MODULE_REMOVED','USER_ROLE_CHANGED','USER_SECTION_CHANGED','MODULE_STATUS_CHANGED','SECTION_UPDATED'} <= {e['action'] for e in events}
        with psycopg.connect(dsn,autocommit=True) as conn:
            conn.execute("""CREATE FUNCTION reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'simulated audit failure'; END $$;
              CREATE TRIGGER fail_audit BEFORE INSERT ON ops_events FOR EACH ROW EXECUTE FUNCTION reject_audit();""")
            response=client.put(f'/api/v1/admin/sections/{section_b}/modules',headers=admin,json={'module_ids':[crb]})
            assert response.status_code==500
            assert conn.execute('SELECT count(*) FROM section_modules WHERE section_id=%s',(section_b,)).fetchone()[0]==0
            from app.notifications.db_service import NotificationDBService
            uid=identities['manager']['id']
            conn.execute("INSERT INTO notifications(user_id,title,message,related_entity) VALUES (%s,'Restricted task','Task details','task'),(%s,'Account notice','Account notice',NULL)",(uid,uid))
            notices=NotificationDBService.get_user_notifications(uid,unread_only=True)
            assert [n['title'] for n in notices]==['Account notice']
            assert NotificationDBService.get_unread_count(uid)==1
            conn.execute('UPDATE users SET is_active=false WHERE id=%s',(uid,))
            assert client.get('/api/v1/me/access',headers=manager).status_code==401
    print('PASS: real SQL migration/replay, shared grants, both admin perspectives, session authorization, immediate revocation, role/section changes, inactive resources, audit payloads, and atomic rollback.')
finally:
    with psycopg.connect(BASE_DSN,autocommit=True) as control:
        control.execute(psycopg.sql.SQL('DROP SCHEMA {} CASCADE').format(psycopg.sql.Identifier(schema)))
