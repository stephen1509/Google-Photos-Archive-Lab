from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

import pytest

import gpa.windows_release_qualification as wrq


class Obj:
    def __init__(self, **kw): self.__dict__.update(kw)
    def to_dict(self): return dict(self.__dict__)


@dataclass
class Spec:
    candidate_id: str = '13.59-win-x64'
    version: str = '13.59'
    platform: str = 'windows'
    architecture: str = 'x64'
    filename: str = 'exiftool-13.59_64.zip'


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _patch_good(monkeypatch, tmp_path: Path):
    cache=tmp_path/'cache';cache.mkdir();archive=cache/'exiftool-13.59_64.zip';archive.write_bytes(b'archive')
    prep=tmp_path/'prep';prep.mkdir();root=prep/'exiftool-13.59_64';root.mkdir();exe=root/'exiftool.exe';exe.write_bytes(b'exe')
    monkeypatch.setattr(wrq,'get_distribution_spec',lambda c:Spec())
    monkeypatch.setattr(wrq,'verify_distribution_archive',lambda p,s:(p.stat().st_size,_sha(p.read_bytes())))
    monkeypatch.setattr(wrq,'qualify_windows_core',lambda *a,**k:Obj(passed=True,production_write_approved=False,google_retirement_approved=False))
    admission=Obj(
        archive_verified=True,errors=[],checks={'distribution_prepared':True,'executable_present':True},version='13.59',
        executable_path=str(exe),prepared_distribution_root=str(root),prepared_distribution_sha256='d'*64,
        observed_size_bytes=archive.stat().st_size,observed_sha256=_sha(archive.read_bytes()),candidate_id='13.59-win-x64',
        platform='windows',architecture='x64',executable_sha256=_sha(exe.read_bytes()),schema='gpa.exiftool-distribution-admission.v1'
    )
    monkeypatch.setattr(wrq,'admit_distribution',lambda *a,**k:admission)
    qual=Obj(passed=True,errors=[],version='13.59',executable_sha256=_sha(exe.read_bytes()),distribution_sha256='d'*64,
             qualification_id='qid',harness='gpa.exiftool-jpeg-roundtrip.v1',schema='gpa.exiftool-qualification.v1')
    monkeypatch.setattr(wrq,'qualify_exiftool_candidate',lambda *a,**k:qual)
    evidence=Obj(ready_for_promotion_review=True,production_write_approved=False)
    monkeypatch.setattr(wrq,'build_exiftool_evidence_bundle',lambda *a,**k:evidence)
    return cache,prep


def test_good_windows_release_session_binds_core_and_exiftool(monkeypatch,tmp_path):
    cache,prep=_patch_good(monkeypatch,tmp_path)
    r=wrq.qualify_windows_release(tmp_path,tmp_path,tmp_path/'m.json',tmp_path/'raw',exiftool_candidate_id='13.59-win-x64',
        exiftool_cache_dir=cache,exiftool_prepare_parent=prep,exiftool_workdir=tmp_path/'work')
    assert r.schema=='gpa.windows-release-qualification.v1' and r.passed
    assert r.production_write_approved is False and r.google_retirement_approved is False
    assert r.format_writer_qualification_complete is False and r.relationship_writer_qualification_complete is False


def test_windows_core_failure_keeps_session_failed(monkeypatch,tmp_path):
    cache,prep=_patch_good(monkeypatch,tmp_path)
    monkeypatch.setattr(wrq,'qualify_windows_core',lambda *a,**k:Obj(passed=False,production_write_approved=False,google_retirement_approved=False))
    r=wrq.qualify_windows_release(tmp_path,tmp_path,tmp_path/'m',tmp_path/'raw',exiftool_candidate_id='13.59-win-x64',
        exiftool_cache_dir=cache,exiftool_prepare_parent=prep,exiftool_workdir=tmp_path/'work')
    assert not r.passed and not r.checks['windows_core_passed']


def test_missing_local_exiftool_is_fail_closed_without_fetch(monkeypatch,tmp_path):
    monkeypatch.setattr(wrq,'get_distribution_spec',lambda c:Spec())
    monkeypatch.setattr(wrq,'qualify_windows_core',lambda *a,**k:Obj(passed=True,production_write_approved=False,google_retirement_approved=False))
    def fail(*a,**k): raise RuntimeError('missing exact archive')
    monkeypatch.setattr(wrq,'verify_distribution_archive',fail)
    r=wrq.qualify_windows_release(tmp_path,tmp_path,tmp_path/'m',tmp_path/'raw',exiftool_candidate_id='13.59-win-x64',
        exiftool_cache_dir=tmp_path/'cache',exiftool_prepare_parent=tmp_path/'prep',exiftool_workdir=tmp_path/'work')
    assert not r.passed and any('missing exact archive' in e for e in r.errors)


def test_fetch_exiftool_uses_pinned_acquisition(monkeypatch,tmp_path):
    cache,prep=_patch_good(monkeypatch,tmp_path)
    archive=cache/'exiftool-13.59_64.zip'
    called={}
    def acq(spec,cache_dir,timeout_seconds): called['yes']=True;return archive,True,False,'https://example.invalid/final'
    monkeypatch.setattr(wrq,'acquire_distribution_archive',acq)
    r=wrq.qualify_windows_release(tmp_path,tmp_path,tmp_path/'m',tmp_path/'raw',exiftool_candidate_id='13.59-win-x64',
        exiftool_cache_dir=cache,exiftool_prepare_parent=prep,exiftool_workdir=tmp_path/'work',fetch_exiftool=True)
    assert called and r.passed


def test_bad_exiftool_admission_stops_before_candidate_execution(monkeypatch,tmp_path):
    cache,prep=_patch_good(monkeypatch,tmp_path)
    bad=Obj(archive_verified=False,errors=['bad'],checks={},version='13.59',executable_path=None,prepared_distribution_root=None,
            prepared_distribution_sha256=None,observed_size_bytes=1,observed_sha256='0'*64,candidate_id='13.59-win-x64',platform='windows',architecture='x64',executable_sha256=None,schema='gpa.exiftool-distribution-admission.v1')
    monkeypatch.setattr(wrq,'admit_distribution',lambda *a,**k:bad)
    monkeypatch.setattr(wrq,'qualify_exiftool_candidate',lambda *a,**k:pytest.fail('must not execute candidate'))
    r=wrq.qualify_windows_release(tmp_path,tmp_path,tmp_path/'m',tmp_path/'raw',exiftool_candidate_id='13.59-win-x64',
        exiftool_cache_dir=cache,exiftool_prepare_parent=prep,exiftool_workdir=tmp_path/'work')
    assert not r.passed and not r.checks['exiftool_archive_exact']


def test_base_qualification_failure_blocks_review(monkeypatch,tmp_path):
    cache,prep=_patch_good(monkeypatch,tmp_path)
    monkeypatch.setattr(wrq,'qualify_exiftool_candidate',lambda *a,**k:Obj(passed=False,errors=['write failed'],version='13.59',executable_sha256='e'*64,distribution_sha256='d'*64,qualification_id='q',harness='h',schema='gpa.exiftool-qualification.v1'))
    r=wrq.qualify_windows_release(tmp_path,tmp_path,tmp_path/'m',tmp_path/'raw',exiftool_candidate_id='13.59-win-x64',
        exiftool_cache_dir=cache,exiftool_prepare_parent=prep,exiftool_workdir=tmp_path/'work')
    assert not r.passed and not r.checks['exiftool_base_qualification_passed']


def test_report_write_is_no_overwrite(monkeypatch,tmp_path):
    cache,prep=_patch_good(monkeypatch,tmp_path)
    r=wrq.qualify_windows_release(tmp_path,tmp_path,tmp_path/'m',tmp_path/'raw',exiftool_candidate_id='13.59-win-x64',
        exiftool_cache_dir=cache,exiftool_prepare_parent=prep,exiftool_workdir=tmp_path/'work')
    out=tmp_path/'report.json';wrq.write_windows_release_qualification_report(out,r)
    assert json.loads(out.read_text())['schema']=='gpa.windows-release-qualification.v1'
    with pytest.raises(FileExistsError): wrq.write_windows_release_qualification_report(out,r)


def test_timeouts_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        wrq.qualify_windows_release(tmp_path,tmp_path,tmp_path/'m',tmp_path/'raw',exiftool_candidate_id='13.59-win-x64',
            exiftool_cache_dir=tmp_path,exiftool_prepare_parent=tmp_path,exiftool_workdir=tmp_path,timeout_seconds=0)

def test_cli_windows_release_writes_report(monkeypatch,tmp_path):
    from gpa.__main__ import main
    good=Obj(passed=True)
    monkeypatch.setattr(wrq,'qualify_windows_release',lambda *a,**k:good)
    # Patch the import target used by the CLI function.
    import gpa.windows_release_qualification as mod
    monkeypatch.setattr(mod,'qualify_windows_release',lambda *a,**k:good)
    def writer(path,report):
        Path(path).write_text('{"schema":"gpa.windows-release-qualification.v1"}\n')
    monkeypatch.setattr(mod,'write_windows_release_qualification_report',writer)
    out=tmp_path/'release.json'
    rc=main(['qualify-windows-release','--primary',str(tmp_path),'--backup',str(tmp_path),'--raw-manifest',str(tmp_path/'m'),
        '--raw-samples-root',str(tmp_path/'raw'),'--exiftool-cache',str(tmp_path/'cache'),'--exiftool-prepare',str(tmp_path/'prep'),
        '--exiftool-workdir',str(tmp_path/'work'),'--report',str(out)])
    assert rc==0 and out.exists()
