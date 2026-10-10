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
        for name in ['campaign-form','archive-form','overview-action','evidence-graph','room-overview']:
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
let v=describe(data);assert.equal(v.passed,0);assert.match(v.message,/No model has passed/);
assert.match(v.progress,/does not prove/);assert.match(v.next,/Previously saved history/);
assert.equal(v.rooms[0].unavailable,1);
data.archive={state:'partial',error_code:'source_disk_budget'};v=describe(data);
assert.equal(v.destination,'history');assert.match(v.next,/retry alone will not/);
data.archive={state:'completed'};assert.match(describe(data).importMessage,/does not mean every device/);
data.shadow.state='stopped';assert.equal(describe(data).title,'Learning test stopped');
'''
        subprocess.run(['node','-e',script],cwd=ROOT,check=True,capture_output=True,text=True)
