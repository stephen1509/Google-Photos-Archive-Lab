from __future__ import annotations
import json, platform, zipfile
from pathlib import Path
import pytest

from gpa.product import create_product_session
from gpa.realworld import (
    PLAN_SCHEMA,DIAGNOSTICS_SCHEMA,SUPPORT_SCHEMA,collect_host_diagnostics,
    prepare_real_world_handoff,create_privacy_support_bundle,
)


def mkzip(path:Path):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('Takeout/Google Photos/Photos from 2023/IMG PRIVATE.jpg',b'private-media')
        z.writestr('Takeout/Google Photos/Photos from 2023/IMG PRIVATE.jpg.json','{"title":"IMG PRIVATE.jpg"}')


def session(tmp_path):
    z=tmp_path/'PERSONAL TAKEOUT 001.zip';mkzip(z)
    s=create_product_session(tmp_path/'workspace',[z],tmp_path/'archive')
    return z,tmp_path/'workspace'/'session.json',s


def test_prepare_handoff_binds_exact_sources_without_copying_takeout(tmp_path):
    z,sp,s=session(tmp_path);before=z.read_bytes();out=tmp_path/'handoff'
    p=prepare_real_world_handoff(sp,out)
    assert p.schema==PLAN_SCHEMA and p.source_integrity_at_plan_time
    assert p.source_binding[0]['sha256']==s.sources[0].sha256
    assert (out/'QUALIFICATION_PLAN.json').is_file() and (out/'RUN_WINDOWS_QUALIFICATION.ps1').is_file()
    assert not list(out.rglob('*.zip')) and z.read_bytes()==before


def test_handoff_never_claims_windows_evidence_or_approval(tmp_path):
    z,sp,s=session(tmp_path);p=prepare_real_world_handoff(sp,tmp_path/'handoff')
    assert p.windows_evidence_collected is False
    assert p.production_write_approved is False and p.google_retirement_approved is False
    assert all(p.safety_invariants.values())


def test_handoff_refuses_nonempty_destination(tmp_path):
    z,sp,s=session(tmp_path);out=tmp_path/'handoff';out.mkdir();(out/'existing').write_text('x')
    with pytest.raises(FileExistsError):prepare_real_world_handoff(sp,out)


def test_handoff_fails_integrity_flag_after_source_mutation(tmp_path):
    z,sp,s=session(tmp_path);z.write_bytes(z.read_bytes()+b'x')
    p=prepare_real_world_handoff(sp,tmp_path/'handoff')
    assert not p.source_integrity_at_plan_time and p.source_integrity_problems


def test_powershell_runner_is_parameterized_and_has_no_takeout_path(tmp_path):
    z,sp,s=session(tmp_path);out=tmp_path/'handoff';prepare_real_world_handoff(sp,out)
    text=(out/'RUN_WINDOWS_QUALIFICATION.ps1').read_text()
    assert 'PERSONAL TAKEOUT' not in text and str(z) not in text
    assert 'Remove-Item' not in text and 'del ' not in text.lower()
    assert 'production' in text.lower() and 'Google deletion' in text


def test_host_diagnostics_never_passes_nonwindows_as_windows_evidence(tmp_path):
    d=collect_host_diagnostics([tmp_path])
    assert d.schema==DIAGNOSTICS_SCHEMA
    if platform.system()!='Windows':
        assert not d.passed_for_windows_handoff and not d.checks['platform_is_windows']
    assert d.production_write_approved is False and d.google_retirement_approved is False


def test_host_diagnostics_redacts_hostname_to_hash(tmp_path):
    d=collect_host_diagnostics([tmp_path]);p=d.to_dict()['platform']
    assert 'hostname' not in p and len(p['hostname_sha256'])==64


def test_privacy_bundle_excludes_paths_names_and_source_bytes(tmp_path):
    z,sp,s=session(tmp_path)
    report=tmp_path/'report.json';report.write_text(json.dumps({'schema':'example','passed':False,'source_path':str(z),'notes':['secret note'],'checks':{'x':True},'errors':[]}))
    out=tmp_path/'support.zip';m=create_privacy_support_bundle(sp,[report],out)
    assert m['schema']==SUPPORT_SCHEMA and not m['takeout_bytes_included'] and not m['source_paths_included'] and not m['source_filenames_included']
    with zipfile.ZipFile(out) as q:
        payload=b'\n'.join(q.read(n) for n in q.namelist())
    assert b'PERSONAL TAKEOUT' not in payload and str(z).encode() not in payload and b'private-media' not in payload and b'secret note' not in payload
    assert b'\"source_path\":' not in payload and b'/tmp/' not in payload


def test_privacy_bundle_source_hashes_opt_in(tmp_path):
    z,sp,s=session(tmp_path);r=tmp_path/'r.json';r.write_text(json.dumps({'schema':'x','checks':{'a':True}}))
    a=tmp_path/'a.zip';m=create_privacy_support_bundle(sp,[r],a)
    with zipfile.ZipFile(a) as q:d=json.loads(q.read('manifest.json'))
    assert 'sha256' not in d['source_summary'][0]
    b=tmp_path/'b.zip';create_privacy_support_bundle(sp,[r],b,include_source_hashes=True)
    with zipfile.ZipFile(b) as q:d2=json.loads(q.read('manifest.json'))
    assert d2['source_summary'][0]['sha256']==s.sources[0].sha256


def test_privacy_bundle_rejects_malformed_report_but_still_creates_diagnostic_bundle(tmp_path):
    z,sp,s=session(tmp_path);r=tmp_path/'bad.json';r.write_text('nope');out=tmp_path/'support.zip'
    m=create_privacy_support_bundle(sp,[r],out)
    assert out.is_file() and len(m['rejected_reports'])==1


def test_privacy_bundle_is_no_overwrite(tmp_path):
    z,sp,s=session(tmp_path);out=tmp_path/'support.zip';create_privacy_support_bundle(sp,[],out)
    with pytest.raises(FileExistsError):create_privacy_support_bundle(sp,[],out)
