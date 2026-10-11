"""Network monitoring regression tests; no live targets or database writes."""
import ast
import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from collections import deque
ROOT=Path(__file__).resolve().parents[1]
def load_function(file,name,scope):
 tree=ast.parse((ROOT/file).read_text(encoding='utf-8-sig'))
 fn=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
 fn.decorator_list=[]
 mod=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),fn],type_ignores=[])
 exec(compile(ast.fix_missing_locations(mod),str(file),'exec'),scope)
 return scope[name]
class NetworkRuntimeTests(unittest.IsolatedAsyncioTestCase):
 def test_reverse_tail_handles_utf8_blocks_and_partial_last_line(self):
  reverse=load_function('app/network_sentinel/history_logs.py','reverse_log_lines',{})
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'log.txt'; lines=['one','évidence','three','last']
   path.write_bytes('\r\n'.join(lines).encode())
   self.assertEqual(list(reverse(path,block_size=3)),list(reversed(lines)))
 async def test_probes_start_together(self):
  started=asyncio.Event()
  def ping(*args):
   return 'echo'
  async def tcp(*args):
   started.set(); return 'tcp'
  async def thread(fn,*args):
   await asyncio.wait_for(started.wait(),.5); return fn(*args)
  scope={'asyncio':type('Runtime',(),{'gather':staticmethod(asyncio.gather),'to_thread':staticmethod(thread)}),'ping_once':ping,'parse_ping':lambda x:x,'check_tcp':tcp}
  probe=load_function('app/network_sentinel/checks.py','probe_target',scope)
  self.assertEqual(await probe('test',443,1000,icmp_enabled=True,tcp_enabled=True),['echo','tcp'])
 async def test_disabled_probes_do_not_execute(self):
  scope={'asyncio':asyncio,'ping_once':lambda *a:self.fail('disabled ping'),'parse_ping':lambda x:x,'check_tcp':AsyncMock()}
  probe=load_function('app/network_sentinel/checks.py','probe_target',scope)
  self.assertEqual(await probe('test',None,1000,icmp_enabled=False,tcp_enabled=True),[None,None])
  scope['check_tcp'].assert_not_awaited()
 async def test_websocket_sends_adjacent_service_updates_and_unsubscribes(self):
  class Disconnect(Exception): pass
  queue=asyncio.Queue()
  for sid in ['a','b','c']:queue.put_nowait({'type':'SERVICE_STATUS_UPDATED','data':{'service_id':sid}})
  class Engine:
   def subscribe(self):return queue
   def get_health(self):return {'online':True}
   def unsubscribe(self,q):self.unsubscribed=q is queue
  engine=Engine()
  from types import SimpleNamespace
  sent=[]
  class Socket:
   query_params={}
   app=SimpleNamespace(state=SimpleNamespace(network_sentinel_engine=engine))
   async def accept(self):pass
   async def send_text(self,text):
    import json
    sent.append(json.loads(text))
    if len(sent)==4:raise Disconnect()
  import json
  fn=load_function('app/network_sentinel/router.py','network_ws',dict(asyncio=asyncio,json=json,get_current_user_websocket=AsyncMock(return_value={'id':'user'}),HTTPException=type('HTTPError',(Exception,),{}),WebSocketDisconnect=Disconnect))
  await fn(Socket())
  self.assertEqual([m['data']['service_id'] for m in sent[1:]],['a','b','c'])
  self.assertTrue(engine.unsubscribed)
 def test_log_tail_respects_dates_across_files_and_skips_bad_dates(self):
  reverse=load_function('app/network_sentinel/history_logs.py','reverse_log_lines',{})
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   (root/'network_log_test_2026-10-06.txt').write_text('2026-10-06 10:00:00|old\n2026-10-06 11:00:00|keep1\n',encoding='utf-8')
   (root/'network_log_test_2026-10-07.txt').write_text('2026-10-07 10:00:00|keep2\n2026-99-07 10:00:00|bad\n2026-10-07 11:00:00|future',encoding='utf-8')
   scope=dict(Path=Path,__file__=str(ROOT/'app/network_sentinel/router.py'),default_log_dir=lambda _:root,datetime=datetime,timezone=timezone,deque=deque,reverse_log_lines=reverse,_parse_log_line=lambda line:dict(zip(['timestamp','raw'],line.split('|',1))))
   read=load_function('app/network_sentinel/router.py','_read_service_logs',scope)
   rows=read('test',datetime(2026,10,6,11,tzinfo=timezone.utc),datetime(2026,10,7,10,tzinfo=timezone.utc),limit=2)
   self.assertEqual([row['raw'] for row in rows],['keep1','keep2'])
 async def test_investigation_scopes_aggregates_to_requested_service(self):
  from contextlib import asynccontextmanager
  tree=ast.parse((ROOT/'app/network_sentinel/query_service.py').read_text(encoding='utf-8'))
  sql=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_SERVICE_SNAPSHOT_SQL' for t in n.targets))
  conn=type('Connection',(),{'fetchrow':AsyncMock(return_value=None)})()
  @asynccontextmanager
  async def connection():yield conn
  fn=load_function('app/network_sentinel/query_service.py','fetch_service_investigation',dict(get_async_connection=connection,_SERVICE_SNAPSHOT_SQL=sql))
  self.assertIsNone(await fn('selected-service',horizon_hours=24,sample_limit=100,event_limit=20,outage_limit=20))
  query,service_id=conn.fetchrow.call_args.args
  self.assertEqual(service_id,'selected-service')
  self.assertIn('WHERE service_id = $1 AND sampled_at >=',query)
  self.assertIn('WHERE service_id = $1 AND ended_at IS NULL',query)
  self.assertIn('AND s.id = $1',query)
if __name__=='__main__':unittest.main()
