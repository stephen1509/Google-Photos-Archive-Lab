from __future__ import annotations
import json, zipfile
from pathlib import Path
from gpa.__main__ import main
from gpa.product import load_product_session,build_product_dashboard


def mkzip(path:Path):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('Takeout/Google Photos/Photos from 2023/IMG.jpg',b'x')
        z.writestr('Takeout/Google Photos/Photos from 2023/IMG.jpg.json',json.dumps({'title':'IMG.jpg','photoTakenTime':{'timestamp':'1700000000'}}))


def test_cli_prepares_real_world_handoff_and_preserves_not_ready(tmp_path):
    z=tmp_path/'takeout.zip';mkzip(z);w=tmp_path/'w';archive=tmp_path/'archive'
    assert main(['product-init','--workspace',str(w),'--input',str(z),'--output',str(archive)])==0
    h=tmp_path/'handoff';assert main(['prepare-real-world','--session',str(w/'session.json'),'--output',str(h)])==0
    assert (h/'QUALIFICATION_PLAN.json').is_file()
    d=build_product_dashboard(load_product_session(w/'session.json'))
    assert d.retirement_state=='NOT_READY' and not d.qualification_gates['windows_core_qualified']


def test_cli_host_diagnostics_writes_report_and_fails_closed_here(tmp_path):
    rp=tmp_path/'diag.json';rc=main(['host-diagnostics','--path',str(tmp_path),'--report',str(rp)])
    d=json.loads(rp.read_text());assert d['schema']=='gpa.host-diagnostics.v1'
    assert d['production_write_approved'] is False and d['google_retirement_approved'] is False
    if d['platform']['system']!='Windows':assert rc==1 and d['passed_for_windows_handoff'] is False


def test_cli_privacy_bundle_has_no_source_zip_bytes(tmp_path):
    z=tmp_path/'takeout.zip';mkzip(z);before=z.read_bytes();w=tmp_path/'w';archive=tmp_path/'archive'
    main(['product-init','--workspace',str(w),'--input',str(z),'--output',str(archive)])
    rp=tmp_path/'diag.json';main(['host-diagnostics','--path',str(tmp_path),'--report',str(rp)])
    bundle=tmp_path/'support.zip';rc=main(['privacy-support-bundle','--session',str(w/'session.json'),'--report',str(rp),'--output',str(bundle)])
    assert rc==0 and bundle.is_file() and z.read_bytes()==before
    with zipfile.ZipFile(bundle) as q:payload=b''.join(q.read(n) for n in q.namelist())
    assert before not in payload and str(z).encode() not in payload
