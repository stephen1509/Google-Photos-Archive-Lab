from __future__ import annotations
import json, zipfile
from pathlib import Path

from gpa.__main__ import main
from gpa.product import load_product_session,build_product_dashboard
from gpa.executor import RunExecutor


def mkzip(path:Path, entries:dict[str,bytes|str]):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for k,v in entries.items():z.writestr(k,v.encode() if isinstance(v,str) else v)


def make_takeout(path:Path):
    meta=json.dumps({'title':'IMG_1.jpg','photoTakenTime':{'timestamp':'1700000000'}})
    mkzip(path,{'Takeout/Google Photos/Photos from 2023/IMG_1.jpg':b'jpeg-bytes','Takeout/Google Photos/Photos from 2023/IMG_1.jpg.json':meta})


def test_cli_product_init_qualify_set_status(tmp_path):
    z=tmp_path/'takeout-001.zip';make_takeout(z);w=tmp_path/'workspace';out=tmp_path/'archive';q=tmp_path/'q.json'
    assert main(['product-init','--workspace',str(w),'--input',str(z),'--output',str(out)])==0
    assert main(['qualify-real-takeout','--input',str(z),'--output',str(out),'--report',str(q)])==0
    assert main(['product-set','--session',str(w/'session.json'),'--real-takeout-qualification',str(q)])==0
    report=tmp_path/'status.json'
    assert main(['product-status','--session',str(w/'session.json'),'--report',str(report)])==1
    d=json.loads(report.read_text())
    assert d['qualification_gates']['real_takeout_qualified'] is True
    assert d['retirement_state']=='NOT_READY'


def test_product_status_after_real_migration_still_fails_closed(tmp_path):
    z=tmp_path/'takeout-001.zip';make_takeout(z);w=tmp_path/'workspace';out=tmp_path/'archive';q=tmp_path/'q.json'
    main(['product-init','--workspace',str(w),'--input',str(z),'--output',str(out)])
    qual=main(['qualify-real-takeout','--input',str(z),'--output',str(out),'--report',str(q)]);assert qual==0
    main(['product-set','--session',str(w/'session.json'),'--real-takeout-qualification',str(q)])
    m=RunExecutor([z],out).execute(run_id='product-integration')
    assert m['status'] in ('complete','needs_review')
    d=build_product_dashboard(load_product_session(w/'session.json'))
    assert d.archive_exists and d.archive_audit_ok
    assert d.qualification_gates['real_takeout_qualified']
    assert not d.qualification_gates['windows_core_qualified']
    assert not d.qualification_gates['windows_writer_qualified']
    assert d.retirement_state=='NOT_READY'


def test_product_dashboard_blocks_after_source_changed_post_migration(tmp_path):
    z=tmp_path/'takeout-001.zip';make_takeout(z);w=tmp_path/'workspace';out=tmp_path/'archive'
    main(['product-init','--workspace',str(w),'--input',str(z),'--output',str(out)])
    RunExecutor([z],out).execute(run_id='product-source-change')
    z.write_bytes(z.read_bytes()+b'changed')
    d=build_product_dashboard(load_product_session(w/'session.json'))
    assert not d.source_integrity_ok
    assert d.retirement_state=='NOT_READY'


def test_product_set_boolean_can_be_reversed(tmp_path):
    z=tmp_path/'takeout-001.zip';make_takeout(z);w=tmp_path/'workspace';out=tmp_path/'archive'
    main(['product-init','--workspace',str(w),'--input',str(z),'--output',str(out)])
    assert main(['product-set','--session',str(w/'session.json'),'--all-takeout-parts-confirmed'])==0
    assert load_product_session(w/'session.json').all_takeout_parts_confirmed is True
    assert main(['product-set','--session',str(w/'session.json'),'--no-all-takeout-parts-confirmed'])==0
    assert load_product_session(w/'session.json').all_takeout_parts_confirmed is False
