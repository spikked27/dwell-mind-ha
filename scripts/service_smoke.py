"""Runs inside a disposable offline test container. Never prints the pairing key."""
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

base='http://127.0.0.1:8128'
for attempt in range(20):
    try:
        urllib.request.urlopen(base+'/health',timeout=2).close()
        break
    except (OSError,urllib.error.URLError):time.sleep(0.5)
else:raise SystemExit('Worker did not become healthy')
key=Path('/data/service-token').read_text()


def request(path,payload=None):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(base+path,data=data,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)


status=request('/v1/status')
assert status['control_enabled'] is False
with urllib.request.urlopen(base+'/ui',timeout=2) as response:
    assert response.headers.get_content_type()=='text/html'
    assert b'Evidence studio' in response.read()
try:
    urllib.request.urlopen(base+'/v1/status',timeout=2)
    raise AssertionError('Unauthenticated API accepted')
except urllib.error.HTTPError as error:assert error.code==401
request('/v1/config',{'rooms':[{'area_id':'study','name':'Study','entities':['light.study']}]})
status=request('/v1/start',{'duration_seconds':10})
request('/v1/events',{'capture_id':status['capture_id'],'sequence':1,'snapshot':True,
                     'events':[{'event_type':'state_changed','context':{},'data':{'entity_id':'light.study',
                     'new_state':{'state':'off','attributes':{},'context':{}}}}]})
view=request('/v1/context')
assert view['observations']['light.study']['state']=='off'
assert view['context_activity_models_available'] is False
status=request('/v1/stop',{})
report=json.loads((Path('/data/reports')/(status['last_report']+'.json')).read_text())
assert report['preference_labels']==0 and report['all_entities_have_terminal_gap']
summary=request('/v1/report/latest')['summary']
assert summary['report_id']==status['last_report'] and summary['all_entities_have_terminal_gap']
print('Worker pairing, authenticated capture, private report and terminal gaps verified')
