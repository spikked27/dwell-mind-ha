from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class Page(HTMLParser):
    def __init__(self):super().__init__();self.ids=[];self.details=[]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if 'id' in values:self.ids.append(values['id'])
        if tag=='details':self.details.append(values)

class OverviewTests(unittest.TestCase):
    def test_controls_retained_with_unique_ids_and_advanced_views_closed(self):
        page=Page();page.feed((ROOT/'web/index.html').read_text(encoding='utf-8'))
        self.assertEqual(len(page.ids),len(set(page.ids)))
        for name in ['campaign-form','archive-form','overview-action','evidence-graph','room-overview','review-candidates','review-queue']:
            self.assertIn(name,page.ids)
        for name in ['test-settings','context-lab']:
            detail=next(v for v in page.details if v.get('id')==name)
            self.assertNotIn('open',detail)

    @unittest.skipUnless(shutil.which('node'),'Node needed for pure UI model checks')
    def test_plain_language_summary_never_promotes_matches_or_missing_history(self):
        script=r'''
const assert=require('node:assert/strict');
const describe=require('./web/overview.js');
const data={status:{state:'capturing'},scope:[{name:'Study',entities:['light.study']}],observations:{},
 shadow:{state:'running',model_targets:1,evaluated:25,matched_reported_outcomes:25,reports:{'light.study|state':{state:'experimental'}}},
 archive:{state:'not_configured'}};
let v=describe(data);assert.equal(v.passed,0);assert.match(v.message,/No candidate has passed/);
assert.match(v.progress,/confirmed: none/);assert.match(v.next,/Previously saved history/);
assert.equal(v.rooms[0].unavailable,1);
assert.equal(v.rooms[0].capabilities[0].stage,'Not yet reliable');
assert.equal(v.rooms[0].capabilities[1].stage,'Not selected');
const candidate={decision_id:'change',target:'light.study',channel:'state',current:'off',predicted:'on',would_change:true,model_state:'beats_baselines',blocked_by:['preference_not_established','execution_not_implemented']};
data.shadow.decisions=[candidate];assert.deepEqual(describe(data).reviewable,['change']);
for(const change of [{would_change:false},{predicted:'off'},{model_state:'experimental'},{blocked_by:['manual_override']},{target:'binary_sensor.study'},{target:'sensor.study'}]){
 data.shadow.decisions=[{...candidate,...change}];assert.deepEqual(describe(data).reviewable,[]);
}
data.shadow.decisions=[{...candidate,decision_id:'new',would_change:false},candidate];assert.deepEqual(describe(data).reviewable,[]);
data.shadow.reports['light.study|state'].state='beats_baselines';
assert.equal(describe(data).rooms[0].capabilities[0].stage,'Behavior pattern validated');
data.shadow.preference_reports={'light.study|state':{state:'validated_preference_candidate'}};
assert.equal(describe(data).rooms[0].capabilities[0].stage,'Preference candidate');
data.archive={state:'partial',error_code:'source_disk_budget'};v=describe(data);
assert.equal(v.destination,'history');assert.match(v.next,/retry alone will not/);
data.archive={state:'completed'};assert.match(describe(data).importMessage,/does not mean every device/);
data.shadow.state='stopped';assert.equal(describe(data).title,'Learning test stopped');
'''
        subprocess.run(['node','-e',script],cwd=ROOT,check=True,capture_output=True,text=True)
