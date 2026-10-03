"""System administration of section entitlements, with atomic operational audit."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.auth.service import get_current_user
from app.db.database import get_connection
from .policy import resolve_access
from .service import audit, replace_assignments, require_system_admin

router = APIRouter(tags=['Access management'])


def require_admin(user=Depends(get_current_user)):
    return require_system_admin(user)


@router.get('/me/access')
def my_access(user=Depends(get_current_user)):
    if user.get('access') is not None:
        return user['access']
    with get_connection() as conn:
        return resolve_access(user, conn)


@router.get('/admin/access')
def access_catalog(user=Depends(require_admin)):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT id::text,section_name,is_active FROM sections ORDER BY section_name')
        sections = [dict(id=r[0],name=r[1],is_active=r[2]) for r in cur.fetchall()]
        cur.execute('SELECT id::text,module_key,name,page_key,is_active FROM access_modules ORDER BY page_key,name')
        modules = [dict(id=r[0],module_key=r[1],name=r[2],page_key=r[3],is_active=r[4]) for r in cur.fetchall()]
        cur.execute('SELECT section_id::text,module_id::text FROM section_modules')
        return dict(sections=sections, modules=modules,
                    assignments=[dict(section_id=r[0],module_id=r[1]) for r in cur.fetchall()])


class ModuleAssignments(BaseModel):
    module_ids: list[UUID]


class SectionAssignments(BaseModel):
    section_ids: list[UUID]


@router.put('/admin/sections/{section_id}/modules')
def set_section_modules(section_id: UUID, payload: ModuleAssignments, user=Depends(require_admin)):
    with get_connection() as conn, conn.cursor() as cur:
        # Serialize both perspectives so concurrent edits cannot lose shared grants.
        cur.execute('SELECT pg_advisory_xact_lock(19780916)')
        replace_assignments(cur, user, section_id, {str(v) for v in payload.module_ids})
    return {'saved': True}


@router.put('/admin/modules/{module_id}/sections')
def set_module_sections(module_id: UUID, payload: SectionAssignments, user=Depends(require_admin)):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(19780916)')
        cur.execute('SELECT id FROM access_modules WHERE id=%s', (str(module_id),))
        if not cur.fetchone():
            raise HTTPException(404, 'Module not found.')
        cur.execute('SELECT id::text FROM sections ORDER BY id')
        sections = {r[0] for r in cur.fetchall()}
        desired = {str(v) for v in payload.section_ids}
        if not desired <= sections:
            raise HTTPException(422, 'Unknown section ID.')
        for section_id in sorted(sections):
            cur.execute('SELECT module_id::text FROM section_modules WHERE section_id=%s', (section_id,))
            keys = {r[0] for r in cur.fetchall()}
            keys.add(str(module_id)) if section_id in desired else keys.discard(str(module_id))
            replace_assignments(cur,user,section_id,keys)
    return {'saved': True}


class SectionInput(BaseModel):
    name: str = Field(min_length=1,max_length=150)
    is_active: bool = True


class ModuleStatus(BaseModel):
    is_active: bool


@router.patch('/admin/modules/{module_id}')
def set_module_status(module_id: UUID, payload: ModuleStatus, user=Depends(require_admin)):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT is_active FROM access_modules WHERE id=%s FOR UPDATE',(str(module_id),))
        previous = cur.fetchone()
        if not previous:
            raise HTTPException(404,'Module not found.')
        cur.execute('UPDATE access_modules SET is_active=%s,updated_at=now() WHERE id=%s',(payload.is_active,str(module_id)))
        if previous[0] != payload.is_active:
            audit(cur,user,'MODULE_STATUS_CHANGED',module_id,module=module_id,old_value=previous[0],new_value=payload.is_active)
    return {'saved':True}


@router.get('/admin/access/audit')
def access_audit(user=Depends(require_admin)):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT event_type,entity_id::text,payload,created_at FROM ops_events WHERE entity_type='ACCESS_MANAGEMENT' ORDER BY created_at DESC LIMIT 100")
        return [dict(action=r[0],entity_id=r[1],details=r[2],timestamp=r[3]) for r in cur.fetchall()]


@router.post('/admin/sections', status_code=201)
def create_section(payload: SectionInput,user=Depends(require_admin)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422,'Section name is required.')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(19780916)')
        cur.execute('SELECT 1 FROM sections WHERE lower(section_name)=lower(%s)', (name,))
        if cur.fetchone():
            raise HTTPException(409,'A section with this name already exists.')
        cur.execute('INSERT INTO sections(section_name,is_active) VALUES (%s,%s) RETURNING id::text', (name,payload.is_active))
        section_id = cur.fetchone()[0]
        audit(cur,user,'SECTION_CREATED',section_id,section=section_id,new_value=name)
    return {'id':section_id}


@router.patch('/admin/sections/{section_id}')
def update_section(section_id: UUID,payload: SectionInput,user=Depends(require_admin)):
    if not payload.name.strip():
        raise HTTPException(422,'Section name is required.')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT section_name,is_active FROM sections WHERE id=%s FOR UPDATE',(str(section_id),))
        previous = cur.fetchone()
        if not previous:
            raise HTTPException(404,'Section not found.')
        cur.execute('UPDATE sections SET section_name=%s,is_active=%s WHERE id=%s',(payload.name.strip(),payload.is_active,str(section_id)))
        audit(cur,user,'SECTION_UPDATED',section_id,section=section_id,old_value=previous,new_value=[payload.name.strip(),payload.is_active])
    return {'saved':True}


@router.get('/admin/users/{user_id}/access')
def inspect_user_access(user_id: UUID,user=Depends(require_admin)):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT u.id::text,r.name FROM users u
            LEFT JOIN user_roles ur ON ur.user_id=u.id LEFT JOIN roles r ON r.id=ur.role_id
            WHERE u.id=%s ORDER BY CASE r.name WHEN 'admin' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END LIMIT 1''',(str(user_id),))
        target = cur.fetchone()
        if not target:
            raise HTTPException(404,'User not found.')
        return resolve_access({'id':target[0],'role':target[1]},conn)


def require_module_access(module_key):
    from .policy import MODULE_KEYS
    if module_key not in MODULE_KEYS:
        raise ValueError('Unknown module key: '+module_key)
    def dependency(user=Depends(get_current_user)):
        if module_key not in my_access(user)['modules']:
            raise HTTPException(403,'Your section cannot access this module.')
        return user
    return dependency
