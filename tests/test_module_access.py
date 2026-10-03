import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from app.access.policy import effective_keys, required_modules, MODULE_KEYS, event_modules
from app.access.middleware import ModuleAccessMiddleware


@pytest.mark.parametrize('role', ['ADMIN','admin','MANAGER','USER'])
def test_role_is_independent_of_section(role):
    result=effective_keys(role,True,['trustlink.run_history'],['trustlink.run_history','reports.crb'])
    assert ('reports.crb' in result) == (role.upper()=='ADMIN')
    assert 'trustlink.run_history' in result


def test_shared_sections_multiple_modules_and_revocation():
    active=['trustlink.daily_extraction','trustlink.run_history','reports.crb']
    a=['trustlink.daily_extraction','trustlink.run_history']
    b=['trustlink.run_history','reports.crb']
    assert effective_keys('USER',True,a,active) & effective_keys('MANAGER',True,b,active) == {'trustlink.run_history'}
    a.remove('trustlink.run_history')
    assert 'trustlink.run_history' not in effective_keys('USER',True,a,active)
    assert 'trustlink.run_history' in effective_keys('USER',True,b,active)


@pytest.mark.parametrize('active_section',[False,None])
def test_no_section_and_inactive_section(active_section):
    assert not effective_keys('MANAGER',active_section,['reports.crb'],['reports.crb'])


def test_inactive_module_denied_even_to_admin():
    assert not effective_keys('ADMIN',False,['reports.crb'],[])


def test_complete_route_inventory_uses_registered_keys():
    import ast
    base=Path(__file__).resolve().parents[1]
    files=[base/'app'/folder/'router.py' for folder in ['checklists','tasks','network_sentinel','trustlink','gamification']]
    files += [base.parent/'sentinelops-ai/app/api'/f for f in ['nexus.py','nexus_clearing.py','nexus_reports.py']]
    checked=0
    for file in files:
        tree=ast.parse(file.read_text(encoding='utf-8'))
        prefix=''
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='APIRouter':
                prefix=next((k.value.value for k in node.keywords if k.arg=='prefix'),'')
        for node in ast.walk(tree):
            if isinstance(node,(ast.AsyncFunctionDef,ast.FunctionDef)):
                for dec in node.decorator_list:
                    if not isinstance(dec,ast.Call) or not isinstance(dec.func,ast.Attribute) or dec.func.attr not in ['get','post','patch','put','delete','websocket']:
                        continue
                    route='/api/v1'+prefix+dec.args[0].value
                    keys=required_modules(route,dec.func.attr.upper())
                    assert set(keys)<=MODULE_KEYS,(route,keys)
                    if not keys:
                        assert route.endswith(('/authorization-policy','/state-policy','/heartbeat','/probe-report','/config','/diagnostic-results','/control-results')),route
                    checked+=1
    assert checked>100


class State:
    role='USER'
    assigned={'reports.crb'}
    section_active=True
    module_active=True


@contextmanager
def connect():
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,*a): pass
        def fetchall(self):
            return [(key,State.module_active,'section-a','Section A',State.section_active,key in State.assigned)
                    for key in MODULE_KEYS]
    class Conn:
        def cursor(self):return Cursor()
    yield Conn()


async def authenticate(header):
    if header!='Bearer valid': raise HTTPException(401,'Missing or invalid token')
    return {'id':'test-user','role':State.role}


@pytest.fixture
def client():
    State.role='USER';State.assigned={'reports.crb'};State.section_active=True;State.module_active=True
    app=FastAPI()
    app.add_middleware(ModuleAccessMiddleware,authenticate=authenticate,connect=connect)
    @app.get('/api/v1/nexus/reports/crb/overview')
    def read(): return {'secret':'entitled data'}
    @app.post('/api/v1/nexus/reports/crb/extractions')
    def write():
        if State.role not in ['ADMIN','MANAGER']:raise HTTPException(403,'Manager required')
        return {'ok':True}
    return TestClient(app)


def test_unauthenticated_401(client):
    assert client.get('/api/v1/nexus/reports/crb/overview').status_code==401


def test_direct_api_deny_and_immediate_reassignment(client):
    headers={'Authorization':'Bearer valid'}
    url='/api/v1/nexus/reports/crb/overview'
    assert client.get(url,headers=headers).status_code==200
    State.assigned=set()
    response=client.get(url,headers=headers)
    assert response.status_code==403 and 'secret' not in response.text
    State.assigned={'reports.crb'}
    assert client.get(url,headers=headers).status_code==200
    State.section_active=False
    assert client.get(url,headers=headers).status_code==403


def test_role_restriction_remains_inside_module(client):
    url='/api/v1/nexus/reports/crb/extractions';headers={'Authorization':'Bearer valid'}
    assert client.post(url,headers=headers,json={'role':'ADMIN','modules':['reports.crb']}).status_code==403
    State.role='MANAGER'
    assert client.post(url,headers=headers).status_code==200
    State.assigned=set()
    assert client.post(url,headers=headers).status_code==403
    State.role='ADMIN'
    assert client.post(url,headers=headers).status_code==200


def test_mixed_realtime_stream_classification():
    path='/api/v1/checklists/ws'
    assert event_modules(path,{'type':'trustlink_update'})==('trustlink.daily_extraction','trustlink.run_history')
    assert event_modules(path,{'type':'ITEM_UPDATED'})==('checklists.execution',)
    assert event_modules(path,{'type':'CHECKLIST_UPDATE','data':{'type':'trustlink_update'}})==('trustlink.daily_extraction','trustlink.run_history')
    assert event_modules(path,{'type':'CHECKLIST_UPDATE','data':{'type':'trustlink_update','event':'pipeline_config'}})==('trustlink.daily_extraction','trustlink.configuration')


@pytest.mark.parametrize('historic,history_access,expected', [(False,False,True),(True,False,False),(True,True,True)])
def test_daily_stream_cannot_leak_historical_runs(historic, history_access, expected):
    from datetime import date, timedelta
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,params): assert params == ('run-a',)
        def fetchone(self): return (date.today()-timedelta(days=int(historic)),)
    class Connection(Cursor):
        def cursor(self): return Cursor()
    middleware=ModuleAccessMiddleware(None,authenticate,Connection)
    keys=['trustlink.daily_extraction'] + (['trustlink.run_history'] if history_access else [])
    payload={'type':'CHECKLIST_UPDATE','data':{'type':'trustlink_update','event':'step','run_id':'run-a'}}
    assert middleware.can_receive_event('/api/v1/checklists/ws',payload,keys) is expected


def test_combined_network_response_does_not_expose_other_modules():
    from app.access.projections import project_network
    source={'services':[{'id':'a','status':{'overall_status':'DOWN'},'metrics':{'uptime':99},'active_outage':{'id':'secret'}}],
            'samples':[{'secret':'signal'}],'raw_rows':['secret'],'events':[{'secret':'event'}],'outages':[{'secret':'outage'}],
            'metrics':{'availability':99,'outage_count_diagnostic_window':4},'overview':{'recent_event_count':7}}
    config=project_network(source,['network_sentinel.configuration'])
    assert config['services'][0]['id']=='a'
    assert config['services'][0]['status'] is None
    assert not config['samples'] and not config['events'] and not config['outages']
    history=project_network(source,['network_sentinel.outage_history'])
    assert history['events'] and history['outages'] and not history['raw_rows']
    monitoring=project_network(source,['network_sentinel.monitoring'])
    assert monitoring['samples'] and not monitoring['outages']
    assert source['outages']  # Do not mutate shared engine state.


def test_websocket_auth_and_revocation():
    from starlette.websockets import WebSocketDisconnect
    app=FastAPI()
    app.add_middleware(ModuleAccessMiddleware,authenticate=authenticate,connect=connect)
    @app.websocket('/api/v1/tasks/ws')
    async def socket(ws: __import__('fastapi').WebSocket):
        await ws.accept()
        while True:
            try:
                await ws.receive_text()
                await ws.send_json({'type':'TASK_UPDATED','data':'protected'})
            except WebSocketDisconnect:
                break
    State.role='USER';State.assigned={'task_manager.tasks'};State.section_active=True;State.module_active=True
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/v1/tasks/ws'):
                pass
        with client.websocket_connect('/api/v1/tasks/ws?token=valid') as ws:
            ws.send_text('next')
            assert ws.receive_json()['data']=='protected'
            State.assigned=set()
            ws.send_text('next')
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_notification_entitlements():
    from app.access.notifications import visible
    assert not visible({'related_entity':'nexus_incident'},['checklists.execution'])
    assert not visible({'related_entity':'task'},[])
    assert visible({'related_entity':'checklist_item'},['checklists.execution'])
    assert visible({'related_entity':None},[])


def test_admin_role_boundary_and_audit_payload():
    from app.access.service import require_system_admin as require_admin, audit
    for role in ('USER','MANAGER'):
        with pytest.raises(HTTPException) as exc:
            require_admin({'id':'actor','role':role})
        assert exc.value.status_code==403
    assert require_admin({'id':'actor','role':'admin'})['id']=='actor'
    class Cursor:
        def execute(self,sql,params):
            self.sql,self.params=sql,params
    cur=Cursor()
    audit(cur,{'id':'actor','username':'admin'},'MODULE_ASSIGNED','section-a',section='section-a',module='module-a',old_value=False,new_value=True)
    assert 'ops_events' in cur.sql
    payload=json.loads(cur.params[2])
    assert payload['actor']=='actor' and payload['old_value'] is False and payload['new_value'] is True


def test_contract_copies_match():
    root=Path(__file__).resolve().parents[2]
    for name in ['policy.py','middleware.py','modules.json','notifications.py','projections.py','request_context.py']:
        assert (root/'SentinelOps-beta/app/access'/name).read_bytes()==(root/'sentinelops-ai/app/access'/name).read_bytes()


def test_embedded_access_reuses_identity_only_within_matching_request():
    from app.access.request_context import authenticated_user
    calls = []

    async def auth(header):
        calls.append(header)
        return {'id': header, 'role': 'USER', 'access': {'modules': ['reports.crb']}}

    def unexpected_connection():
        pytest.fail('Access already resolved by authentication: no second connection')

    async def handler(scope, receive, send):
        header = dict(scope['headers'])[b'authorization'].decode()
        await asyncio.sleep(0)  # Interleave requests to prove identities are isolated.
        assert authenticated_user(header)['id'] == header
        assert authenticated_user('Bearer another-user') is None
        assert scope['state']['module_access']['modules'] == ['reports.crb']

    middleware = ModuleAccessMiddleware(handler, auth, unexpected_connection)

    async def request(token):
        scope = {'type': 'http', 'path': '/api/v1/nexus/reports/crb/overview',
                 'method': 'GET', 'headers': [(b'authorization', token.encode())]}
        await middleware(scope, None, None)
        assert authenticated_user(token) is None

    async def concurrent_requests():
        await asyncio.gather(request('Bearer one'), request('Bearer two'))
        await request('Bearer one')
    asyncio.run(concurrent_requests())
    assert calls == ['Bearer one', 'Bearer two', 'Bearer one']


def test_request_identity_cleared_when_handler_raises():
    from app.access.request_context import authenticated_user
    async def auth(header):
        return {'id': 'one', 'access': {'modules': ['reports.crb']}}
    async def handler(*args):
        assert authenticated_user('Bearer one')['id'] == 'one'
        raise RuntimeError('handler failed')
    middleware = ModuleAccessMiddleware(handler, auth, None)
    async def request():
        with pytest.raises(RuntimeError, match='handler failed'):
            await middleware({'type': 'http', 'path': '/api/v1/nexus/reports/crb/overview',
                              'method': 'GET', 'headers': [(b'authorization', b'Bearer one')]}, None, None)
        assert authenticated_user('Bearer one') is None
    asyncio.run(request())


def test_full_network_access_preserves_streaming_response():
    sent = []
    messages = [
        {'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]},
        {'type': 'http.response.body', 'body': b'{"services":', 'more_body': True},
        {'type': 'http.response.body', 'body': b'[]}', 'more_body': False},
    ]
    async def auth(header):
        return {'access': {'modules': ['network_sentinel.monitoring', 'network_sentinel.outage_history']}}
    async def handler(scope, receive, send):
        for message in messages:
            await send(message)
            assert sent[-1] is message  # Sent immediately, not buffered/re-serialized.
    async def send(message):
        sent.append(message)
    middleware = ModuleAccessMiddleware(handler, auth, None)
    asyncio.run(middleware({'type': 'http', 'path': '/api/v1/network-sentinel/overview',
                            'method': 'GET', 'headers': []}, None, send))
    assert sent == messages
