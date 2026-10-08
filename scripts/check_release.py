"""Static public export checks. Run on tracked/intended source, never appdata."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from container_app import environment_config


def main():
    paths = subprocess.run(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,
                           capture_output=True,text=True,check=True).stdout.splitlines()
    forbidden = {'private','journal','captures','reports','appdata','.venv'}
    for name in paths:
        path = Path(name)
        if forbidden.intersection(path.parts) or path.name in {'.env','config.json','live-observer.json'} or path.suffix == '.jsonl':
            raise SystemExit('Private artifact in public export: ' + name)
        content = (ROOT/path).read_text(encoding='utf-8')
        if path.suffix == '.py':
            ast.parse(content,filename=name)
        if path.suffix == '.json':
            json.loads(content)
        # No installation endpoint literals or actual HA/GitHub token shapes.
        if re.search(r'\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b',content):
            raise SystemExit('Private address literal in public export: '+name)
        if re.search(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})',content):
            raise SystemExit('Possible credential in public export: '+name)
    root = ET.parse(ROOT/'unraid/dwellmind-ha.xml').getroot()
    assert root.tag=='Container' and root.attrib['version']=='2'
    assert root.findtext('Privileged')=='false'
    assert root.findtext('Repository')=='ghcr.io/spikked27/dwell-mind-ha:edge'
    configs=root.findall('Config')
    targets=[c.attrib['Target'] for c in configs]
    assert len(targets)==len(set(targets))
    assert not any(c.attrib['Target']=='HA_TOKEN' or c.attrib['Target'].startswith(('OFFICE_','LIVING_ROOM_')) for c in configs)
    assert sum(c.attrib['Type']=='Port' for c in configs)==1
    env={c.attrib['Target']:c.text or c.attrib.get('Default','') for c in configs if c.attrib['Type']=='Variable'}
    assert env['RUN_MODE']=='service'
    assert '--memory=256m' in root.findtext('ExtraParams')
    assert '--memory-swap=256m' in root.findtext('ExtraParams')
    manifest=json.loads((ROOT/'custom_components/dwellmind/manifest.json').read_text())
    assert manifest['domain']=='dwellmind' and manifest['config_flow'] is True
    # Relative Markdown references should resolve in the public source tree.
    for name in paths:
        if not name.endswith('.md'):
            continue
        text=(ROOT/name).read_text(encoding='utf-8')
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
            if '://' in target or target.startswith('#'):
                continue
            clean=target.split('#',1)[0]
            if not (ROOT/name).parent.joinpath(clean).exists():
                raise SystemExit('Broken documentation link: '+name+' -> '+target)
    print(json.dumps({'public_files_checked':len(paths),'unraid_template_valid':True,'entity_selection_in_ha':True}))


if __name__=='__main__':
    main()
