from __future__ import annotations
import json, zipfile
from pathlib import Path
import pytest

from gpa.product import (
    SESSION_SCHEMA,QUALIFICATION_SCHEMA,SourceFingerprint,ProductSession,
    fingerprint_source,create_product_session,load_product_session,
    verify_session_sources,qualify_real_takeout,build_product_dashboard,
)
from gpa.desktop import dashboard_rows


def mkzip(path:Path, entries:dict[str,bytes|str]):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for k,v in entries.items():z.writestr(k,v.encode() if isinstance(v,str) else v)


def sidecar(title='IMG_1.jpg'):
    return json.dumps({'title':title,'photoTakenTime':{'timestamp':'1700000000'},'creationTime':{'timestamp':'1700000001'},'geoData':{'latitude':0,'longitude':0},'geoDataExif':{'latitude':0,'longitude':0}})


def tiny_takeout(path:Path):
    mkzip(path,{
        'Takeout/Google Photos/Photos from 2023/IMG_1.jpg':b'not-a-real-jpeg-but-preservable',
        'Takeout/Google Photos/Photos from 2023/IMG_1.jpg.json':sidecar(),
    })


def test_fingerprint_source_is_stable_and_read_only(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z);before=z.read_bytes()
    a=fingerprint_source(z);b=fingerprint_source(z)
    assert a.sha256==b.sha256 and a.size==len(before) and a.zip_members==2
    assert z.read_bytes()==before


def test_fingerprint_rejects_non_zip(tmp_path):
    p=tmp_path/'x.txt';p.write_text('x')
    with pytest.raises(ValueError):fingerprint_source(p)


def test_fingerprint_rejects_invalid_zip(tmp_path):
    p=tmp_path/'x.zip';p.write_bytes(b'not zip')
    with pytest.raises(zipfile.BadZipFile):fingerprint_source(p)


def test_create_and_load_session(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'workspace',[z],tmp_path/'archive')
    assert s.schema==SESSION_SCHEMA and s.sources[0].sha256
    loaded=load_product_session(tmp_path/'workspace'/'session.json')
    assert loaded.session_id==s.session_id and loaded.sources[0].sha256==s.sources[0].sha256


def test_session_creation_never_overwrites(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z);w=tmp_path/'workspace'
    create_product_session(w,[z],tmp_path/'archive')
    with pytest.raises(FileExistsError):create_product_session(w,[z],tmp_path/'archive2')


def test_session_requires_source(tmp_path):
    with pytest.raises(ValueError):create_product_session(tmp_path/'w',[],tmp_path/'a')


def test_verify_session_sources_detects_change(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'a')
    ok,problems=verify_session_sources(s);assert ok and not problems
    z.write_bytes(z.read_bytes()+b'x')
    ok,problems=verify_session_sources(s);assert not ok and problems


def test_real_takeout_qualification_preserves_source_bytes(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z);before=z.read_bytes()
    q=qualify_real_takeout([z],tmp_path/'archive')
    assert q.schema==QUALIFICATION_SCHEMA and q.source_bytes_unchanged
    assert q.member_count==2 and q.media_members==1 and q.json_members==1
    assert z.read_bytes()==before


def test_real_takeout_qualification_reports_known_part_gap(tmp_path):
    a=tmp_path/'takeout-001.zip';c=tmp_path/'takeout-003.zip';tiny_takeout(a);tiny_takeout(c)
    q=qualify_real_takeout([a,c],tmp_path/'archive')
    assert not q.passed
    assert any('part gap' in x.lower() for x in q.blockers)


def test_real_takeout_qualification_report_is_no_overwrite(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z);rp=tmp_path/'q.json'
    qualify_real_takeout([z],tmp_path/'archive',rp)
    with pytest.raises(FileExistsError):qualify_real_takeout([z],tmp_path/'archive',rp)


def test_dashboard_fails_closed_with_only_session(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    d=build_product_dashboard(s)
    assert d.retirement_state=='NOT_READY'
    assert not d.qualification_gates['archive_audit']
    assert not d.qualification_gates['real_takeout_qualified']
    assert not d.qualification_gates['windows_core_qualified']
    assert not d.qualification_gates['windows_writer_qualified']


def test_dashboard_detects_source_mutation(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    z.write_bytes(z.read_bytes()+b'x')
    d=build_product_dashboard(s)
    assert not d.source_integrity_ok and not d.qualification_gates['source_integrity']
    assert d.retirement_state=='NOT_READY'


def test_dashboard_apple_gate_is_conditional(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive',require_apple_photos=True)
    d=build_product_dashboard(s)
    assert 'apple_photos_qualified' in d.qualification_gates and not d.qualification_gates['apple_photos_qualified']


def test_qualification_file_must_have_expected_schema_and_pass(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    fake=tmp_path/'fake.json';fake.write_text(json.dumps({'schema':'wrong','passed':True}))
    s.real_takeout_qualification=str(fake)
    d=build_product_dashboard(s)
    assert not d.qualification_gates['real_takeout_qualified']


def test_dashboard_rows_are_view_only_projection():
    class D:qualification_gates={'a':True,'b':False}
    assert dashboard_rows(D())==[('a','PASS'),('b','BLOCKED')]


def test_real_takeout_report_is_bound_to_exact_session_sources(tmp_path):
    z1=tmp_path/'takeout-001.zip';z2=tmp_path/'other-001.zip';tiny_takeout(z1);mkzip(z2,{'Takeout/Google Photos/Photos from 2023/DIFFERENT.jpg':b'different'})
    qpath=tmp_path/'q.json';qualify_real_takeout([z1],tmp_path/'out1',qpath)
    s=create_product_session(tmp_path/'w',[z2],tmp_path/'archive')
    s.real_takeout_qualification=str(qpath)
    d=build_product_dashboard(s)
    assert not d.qualification_gates['real_takeout_qualified']


def test_real_takeout_report_for_same_sources_can_pass_gate(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    qpath=tmp_path/'q.json';q=qualify_real_takeout([z],tmp_path/'out1',qpath);assert q.passed
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    s.real_takeout_qualification=str(qpath)
    d=build_product_dashboard(s)
    assert d.qualification_gates['real_takeout_qualified']


def test_forged_windows_report_without_checks_is_rejected(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    fake=tmp_path/'win.json';fake.write_text(json.dumps({'schema':'gpa.windows-core-qualification.v1','passed':True,'errors':[],'production_write_approved':False,'google_retirement_approved':False,'platform':{'system':'Windows','windows_x64_eligible':True}}))
    s.windows_core_qualification=str(fake)
    d=build_product_dashboard(s)
    assert not d.qualification_gates['windows_core_qualified']


def test_forged_windows_report_on_nonwindows_platform_is_rejected(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    fake=tmp_path/'win.json';fake.write_text(json.dumps({'schema':'gpa.windows-core-qualification.v1','passed':True,'errors':[],'production_write_approved':False,'google_retirement_approved':False,'checks':{'x':True},'platform':{'system':'Linux','windows_x64_eligible':False}}))
    s.windows_core_qualification=str(fake)
    d=build_product_dashboard(s)
    assert not d.qualification_gates['windows_core_qualified']


def test_update_product_session_roundtrip(tmp_path):
    from gpa.product import update_product_session
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    q=tmp_path/'q.json';q.write_text('{}')
    updated=update_product_session(tmp_path/'w'/'session.json',real_takeout_qualification=str(q),all_takeout_parts_confirmed=True,notes=['x'])
    loaded=load_product_session(tmp_path/'w'/'session.json')
    assert updated.all_takeout_parts_confirmed and loaded.real_takeout_qualification==str(q) and loaded.notes==['x']


def test_update_product_session_rejects_unknown_field(tmp_path):
    from gpa.product import update_product_session
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    with pytest.raises(ValueError):update_product_session(tmp_path/'w'/'session.json',dangerous_override=True)


def test_session_rejects_duplicate_source_path(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    with pytest.raises(ValueError):create_product_session(tmp_path/'w',[z,z],tmp_path/'archive')


def test_real_takeout_qualification_fails_closed_on_unclassified_material(tmp_path):
    z=tmp_path/'takeout-001.zip';mkzip(z,{'Takeout/Google Photos/mystery.future':b'opaque'})
    q=qualify_real_takeout([z],tmp_path/'archive')
    assert not q.passed and q.preview['unclassified_items']==1
    assert any('unclassified' in x.lower() for x in q.blockers)


def test_dashboard_preview_gate_requires_ready_not_needs_review(tmp_path):
    z=tmp_path/'takeout-001.zip';mkzip(z,{'Takeout/Google Photos/mystery.future':b'opaque'})
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    d=build_product_dashboard(s)
    assert d.preview_status=='needs_review'
    assert not d.qualification_gates['read_only_preview']


def test_missing_qualification_file_fails_closed(tmp_path):
    z=tmp_path/'takeout-001.zip';tiny_takeout(z)
    s=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    s.real_takeout_qualification=str(tmp_path/'missing.json')
    d=build_product_dashboard(s)
    assert not d.qualification_gates['real_takeout_qualified']


def test_malformed_session_schema_is_rejected(tmp_path):
    p=tmp_path/'session.json';p.write_text(json.dumps({'schema':'future'}))
    with pytest.raises(ValueError):load_product_session(p)


def test_session_update_replace_failure_leaves_previous_session_intact(tmp_path,monkeypatch):
    from gpa.product import update_product_session
    import gpa.product as product
    z=tmp_path/'takeout-001.zip';tiny_takeout(z);sp=tmp_path/'w'/'session.json'
    original=create_product_session(tmp_path/'w',[z],tmp_path/'archive')
    before=sp.read_bytes()
    def fail_replace(*a,**k):raise OSError('simulated replace failure')
    monkeypatch.setattr(product.os,'replace',fail_replace)
    with pytest.raises(OSError):update_product_session(sp,notes=['new'])
    assert sp.read_bytes()==before
