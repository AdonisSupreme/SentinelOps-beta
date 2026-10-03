"""Transactional access administration; callers own connection lifecycle."""
import json
from fastapi import HTTPException

def audit(cur, actor, action, entity_id, **values):
    payload = {'actor': actor['id'], 'actor_username': actor.get('username'), **values}
    cur.execute('''INSERT INTO ops_events(event_type,entity_type,entity_id,payload,created_at)
        VALUES (%s,'ACCESS_MANAGEMENT',%s,%s,now())''',
        (action, str(entity_id), json.dumps(payload, default=str)))


def replace_assignments(cur, actor, section_id, desired):
    cur.execute('SELECT id FROM sections WHERE id=%s FOR UPDATE', (str(section_id),))
    if not cur.fetchone():
        raise HTTPException(404, 'Section not found.')
    cur.execute('SELECT id::text FROM access_modules')
    valid = {r[0] for r in cur.fetchall()}
    if not desired <= valid:
        raise HTTPException(422, 'Unknown module ID.')
    cur.execute('SELECT module_id::text FROM section_modules WHERE section_id=%s', (str(section_id),))
    previous = {r[0] for r in cur.fetchall()}
    for module_id in sorted(previous ^ desired):
        added = module_id in desired
        if added:
            cur.execute('INSERT INTO section_modules(section_id,module_id,created_by) VALUES (%s,%s,%s)',
                        (str(section_id), module_id, actor['id']))
        else:
            cur.execute('DELETE FROM section_modules WHERE section_id=%s AND module_id=%s',
                        (str(section_id), module_id))
        audit(cur, actor, 'MODULE_ASSIGNED' if added else 'MODULE_REMOVED', section_id,
              section=section_id,module=module_id,old_value=not added,new_value=added)


def require_system_admin(user):
    if str(user.get('role')).upper() != 'ADMIN':
        raise HTTPException(403, 'System administrator access required.')
    return user
