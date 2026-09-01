from __future__ import annotations

from pathlib import Path
import hashlib
import json
from types import SimpleNamespace

import pytest

import gpa.windows_writer_qualification as wwq


class Obj:
    def __init__(self, **kw): self.__dict__.update(kw)
    def to_dict(self): return dict(self.__dict__)


def _sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()


def _good(monkeypatch, tmp_path: Path):
    cache=tmp_path/'cache';cache.mkdir();archive=cache/'exif.zip';archive.write_bytes(b'archive')
    prep=tmp_path/'prep';prep.mkdir();root=prep/'dist';root.mkdir();exe=root/'exiftool.exe';exe.write_bytes(b'exe')
    fixture=tmp_path/'fixture';fixture.mkdir();heic=fixture/wwq.HEIC_FIXTURE.filename;heic.write_bytes(b'h')
    spec=SimpleNamespace(filename='exif.zip',sha256=_sha(b'archive'))
    monkeypatch.setattr(wwq,'get_distribution_spec',lambda c:spec)
    monkeypatch.setattr(wwq,'verify_qualification_fixture',lambda p,s:{'exact_bytes_verified':True})
    base={'schema':'gpa.exiftool-evidence-bundle.v1','candidate_id':'13.59-win-x64','version':'13.59',
          'executable_sha256':_sha(b'exe'),'distribution_sha256':'d'*64,'ready_for_promotion_review':True,
          'production_write_approved':False,'bundle_id':'b'*24,'checks':{'ok':True}}
    admission={'executable_path':str(exe),'prepared_distribution_root':str(root)}
    release=Obj(passed=True,production_write_approved=False,google_retirement_approved=False,
                exiftool_admission=admission,exiftool_base_evidence=base)
    monkeypatch.setattr(wwq,'qualify_windows_release',lambda *a,**k:release)
    def fq(executable,workdir,*,format_profile_id,distribution_root,qualification_fixture,subprocess_timeout_seconds):
        return Obj(passed=True,errors=[],format_profile_id=format_profile_id,version='13.59',
                   executable_sha256=_sha(b'exe'),distribution_sha256='d'*64,production_write_approved=False,
                   schema='gpa.exiftool-format-qualification.v1',qualification_id='q'*24,harness='h',checks={'ok':True})
    monkeypatch.setattr(wwq,'qualify_exiftool_format',fq)
    monkeypatch.setattr(wwq,'build_format_evidence_bundle',lambda base,fq,**k:Obj(
        ready_for_format_promotion_review=True,production_write_approved=False,format_profile_id=fq['format_profile_id'],
        candidate_id='13.59-win-x64',version='13.59',executable_sha256=_sha(b'exe'),distribution_sha256='d'*64,
        bundle_id=(fq['format_profile_id'][:4]+'0'*24)[:24],schema='gpa.exiftool-format-evidence-bundle.v1',checks={'ok':True}))
    def rq_motion(executable,workdir,*,distribution_root,format_profile_id,qualification_fixture,subprocess_timeout_seconds):
        return Obj(passed=True,errors=[],version='13.59',executable_sha256=_sha(b'exe'),distribution_sha256='d'*64,
                   format_profile_id=format_profile_id,secondary_format_profile_id=None,relationship_profile_id='motion-photo-composite-v1',
                   production_write_approved=False,schema='gpa.exiftool-relationship-qualification.v1',qualification_id='r'*24,harness='rh',checks={'ok':True})
    def rq_live(executable,workdir,*,distribution_root,subprocess_timeout_seconds):
        return Obj(passed=True,errors=[],version='13.59',executable_sha256=_sha(b'exe'),distribution_sha256='d'*64,
                   format_profile_id='jpeg-single-v1',secondary_format_profile_id='mov-single-v1',relationship_profile_id='live-photo-pair-v1',
                   production_write_approved=False,schema='gpa.exiftool-relationship-qualification.v1',qualification_id='l'*24,harness='lh',checks={'ok':True})
    monkeypatch.setattr(wwq,'qualify_exiftool_motion_photo',rq_motion)
    monkeypatch.setattr(wwq,'qualify_exiftool_live_photo',rq_live)
    monkeypatch.setattr(wwq,'build_relationship_evidence_bundle',lambda primary,rq,**k:Obj(
        ready_for_relationship_promotion_review=True,production_write_approved=False,bundle_id='z'*24,
        schema='gpa.exiftool-relationship-evidence-bundle.v2',checks={'ok':True}))
    return cache,prep,fixture


def _call(tmp_path,cache,prep,fixture,**kw):
    return wwq.qualify_windows_writer_review(tmp_path,tmp_path,tmp_path/'m',tmp_path/'raw',
        exiftool_candidate_id='13.59-win-x64',exiftool_cache_dir=cache,exiftool_prepare_parent=prep,
        exiftool_workdir=tmp_path/'basework',writer_workdir=tmp_path/'writer',heic_fixture_cache=fixture,**kw)


def test_good_writer_review_binds_all_formats_and_relationships(monkeypatch,tmp_path):
    cache,prep,fixture=_good(monkeypatch,tmp_path)
    r=_call(tmp_path,cache,prep,fixture)
    assert r.passed and r.ready_for_governance_review
    assert len(r.format_evidence)==7 and len(r.relationship_evidence)==4
    assert r.production_write_approved is False and r.google_retirement_approved is False


def test_failed_windows_release_stops_before_writer_execution(monkeypatch,tmp_path):
    cache,prep,fixture=_good(monkeypatch,tmp_path)
    monkeypatch.setattr(wwq,'qualify_windows_release',lambda *a,**k:Obj(passed=False,production_write_approved=False,google_retirement_approved=False))
    monkeypatch.setattr(wwq,'qualify_exiftool_format',lambda *a,**k:pytest.fail('must not run'))
    r=_call(tmp_path,cache,prep,fixture)
    assert not r.passed and not r.checks['windows_release_passed']


def test_live_archive_identity_mismatch_fails_closed(monkeypatch,tmp_path):
    cache,prep,fixture=_good(monkeypatch,tmp_path)
    (cache/'exif.zip').write_bytes(b'wrong')
    r=_call(tmp_path,cache,prep,fixture)
    assert not r.passed and any('live ExifTool/base evidence identity' in e for e in r.errors)


def test_format_failure_blocks_governance_review(monkeypatch,tmp_path):
    cache,prep,fixture=_good(monkeypatch,tmp_path)
    good=wwq.qualify_exiftool_format
    def bad(*a,**k):
        x=good(*a,**k)
        if k['format_profile_id']=='png-single-v1': x.passed=False;x.errors=['bad']
        return x
    monkeypatch.setattr(wwq,'qualify_exiftool_format',bad)
    r=_call(tmp_path,cache,prep,fixture)
    assert not r.passed and r.checks['format:png-single-v1:passed'] is False


def test_candidate_identity_mismatch_is_detected(monkeypatch,tmp_path):
    cache,prep,fixture=_good(monkeypatch,tmp_path)
    monkeypatch.setattr(wwq,'qualify_exiftool_motion_photo',lambda *a,**k:Obj(
        passed=True,errors=[],version='13.58',executable_sha256='e'*64,distribution_sha256='d'*64,
        format_profile_id=k['format_profile_id'],secondary_format_profile_id=None,relationship_profile_id='motion-photo-composite-v1',
        production_write_approved=False,schema='gpa.exiftool-relationship-qualification.v1',qualification_id='r'*24,harness='rh',checks={'ok':True}))
    r=_call(tmp_path,cache,prep,fixture,format_profiles=('jpeg-single-v1',))
    assert not r.passed and r.checks['relationship:motion:jpeg-single-v1:candidate_matches'] is False


def test_profile_selection_can_run_bounded_subset(monkeypatch,tmp_path):
    cache,prep,fixture=_good(monkeypatch,tmp_path)
    r=_call(tmp_path,cache,prep,fixture,format_profiles=('jpeg-single-v1',))
    assert r.passed and set(r.format_evidence)=={'jpeg-single-v1'} and set(r.relationship_evidence)=={'motion:jpeg-single-v1'}


def test_profiles_must_be_unique_and_supported(monkeypatch,tmp_path):
    cache,prep,fixture=_good(monkeypatch,tmp_path)
    with pytest.raises(ValueError): _call(tmp_path,cache,prep,fixture,format_profiles=('jpeg-single-v1','jpeg-single-v1'))
    with pytest.raises(ValueError): _call(tmp_path,cache,prep,fixture,format_profiles=('raw-single-v1',))


def test_report_is_no_overwrite(monkeypatch,tmp_path):
    cache,prep,fixture=_good(monkeypatch,tmp_path)
    r=_call(tmp_path,cache,prep,fixture,format_profiles=('jpeg-single-v1',))
    p=tmp_path/'report.json';wwq.write_windows_writer_qualification_report(p,r)
    assert json.loads(p.read_text())['schema']=='gpa.windows-writer-qualification.v1'
    with pytest.raises(FileExistsError): wwq.write_windows_writer_qualification_report(p,r)
