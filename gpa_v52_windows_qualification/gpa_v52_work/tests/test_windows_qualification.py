from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import gpa.windows_qualification as wq
from gpa.__main__ import main


def _storage(*, good=True):
    return SimpleNamespace(
        separate_physical_device_proven=good,
        separate_physical_media_confirmed=good,
        independence_evidence_source="windows_disk_extents_and_storage_device_descriptor" if good else None,
        physical_identity_conflict=False,
        to_dict=lambda:{"schema":"gpa.storage-identity.v4","separate_physical_device_proven":good},
    )


def _raw(*, good=True):
    return SimpleNamespace(
        passed=good,
        decoder_identity_consistent=good,
        decoder_identity={"rawpy_version":"0.27.0","libraw_version":"0.22.1","validator_sha256":"a"*64} if good else None,
        checks={"all_bytes_unchanged":good},
        to_dict=lambda:{"schema":"gpa.raw-corpus-qualification.v1","passed":good},
    )


def _platform(*, good=True):
    return {
        "system":"Windows" if good else "Linux", "release":"x", "version":"x", "machine":"AMD64",
        "processor":"", "python_version":"3.11", "python_implementation":"CPython",
        "python_executable":"python.exe", "python_executable_sha256":"b"*64,
        "python_pointer_bits":64, "windows_x64_eligible":good,
    }


def setup_paths(tmp_path):
    a=tmp_path/'primary';b=tmp_path/'backup';samples=tmp_path/'samples';
    a.mkdir();b.mkdir();samples.mkdir();m=tmp_path/'manifest.json';m.write_text('{}')
    return a,b,m,samples


def test_windows_core_qualification_passes_only_with_machine_storage_and_raw_evidence(tmp_path,monkeypatch):
    a,b,m,s=setup_paths(tmp_path)
    monkeypatch.setattr(wq,'_platform_evidence',lambda:_platform())
    monkeypatch.setattr(wq,'assess_storage_independence',lambda *a,**k:_storage())
    monkeypatch.setattr(wq,'qualify_raw_corpus',lambda *a,**k:_raw())
    r=wq.qualify_windows_core(a,b,m,s)
    assert r.schema=='gpa.windows-core-qualification.v1' and r.passed
    assert all(r.checks.values()) and not r.production_write_approved and not r.google_retirement_approved


@pytest.mark.parametrize('which', ['platform','storage','raw'])
def test_windows_core_qualification_fails_closed_when_required_evidence_is_missing(tmp_path,monkeypatch,which):
    a,b,m,s=setup_paths(tmp_path)
    monkeypatch.setattr(wq,'_platform_evidence',lambda:_platform(good=which!='platform'))
    monkeypatch.setattr(wq,'assess_storage_independence',lambda *a,**k:_storage(good=which!='storage'))
    monkeypatch.setattr(wq,'qualify_raw_corpus',lambda *a,**k:_raw(good=which!='raw'))
    r=wq.qualify_windows_core(a,b,m,s)
    assert not r.passed


def test_windows_core_requires_real_existing_target_directories(tmp_path,monkeypatch):
    a,b,m,s=setup_paths(tmp_path); a.rmdir()
    monkeypatch.setattr(wq,'_platform_evidence',lambda:_platform())
    monkeypatch.setattr(wq,'assess_storage_independence',lambda *a,**k:_storage())
    monkeypatch.setattr(wq,'qualify_raw_corpus',lambda *a,**k:_raw())
    r=wq.qualify_windows_core(a,b,m,s)
    assert not r.passed and not r.checks['primary_directory_exists']


def test_windows_core_optional_raw_fetch_is_bound_and_failure_blocks(tmp_path,monkeypatch):
    a,b,m,s=setup_paths(tmp_path)
    monkeypatch.setattr(wq,'_platform_evidence',lambda:_platform())
    monkeypatch.setattr(wq,'assess_storage_independence',lambda *a,**k:_storage())
    monkeypatch.setattr(wq,'qualify_raw_corpus',lambda *a,**k:_raw())
    monkeypatch.setattr(wq,'fetch_raw_corpus',lambda *a,**k:SimpleNamespace(passed=False,to_dict=lambda:{'passed':False}))
    r=wq.qualify_windows_core(a,b,m,s,fetch_raw=True)
    assert not r.passed and r.raw_fetch=={'passed':False} and not r.checks['raw_fetch_passed']


def test_windows_core_cli_writes_no_overwrite_report(tmp_path,monkeypatch,capsys):
    a,b,m,s=setup_paths(tmp_path); report=tmp_path/'report.json'
    monkeypatch.setattr(wq,'_platform_evidence',lambda:_platform())
    monkeypatch.setattr(wq,'assess_storage_independence',lambda *a,**k:_storage())
    monkeypatch.setattr(wq,'qualify_raw_corpus',lambda *a,**k:_raw())
    rc=main(['qualify-windows-core','--primary',str(a),'--backup',str(b),'--raw-manifest',str(m),'--raw-samples-root',str(s),'--report',str(report)])
    assert rc==0 and json.loads(report.read_text())['passed'] is True
    rc2=main(['qualify-windows-core','--primary',str(a),'--backup',str(b),'--raw-manifest',str(m),'--raw-samples-root',str(s),'--report',str(report)])
    assert rc2==2
    capsys.readouterr()
