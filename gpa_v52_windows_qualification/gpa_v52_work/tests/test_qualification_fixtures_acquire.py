from __future__ import annotations

import io
from pathlib import Path

import pytest

from gpa.qualification_fixtures import (
    QualificationFixtureError,
    QualificationFixtureSpec,
    acquire_qualification_fixture,
)


def _spec(data: bytes, *, url: str = "https://example.invalid/f.bin") -> QualificationFixtureSpec:
    import hashlib
    return QualificationFixtureSpec(
        fixture_id="test-fixture", format_profile_id="heic-single-v1", filename="f.bin",
        source_repository="example/repo", source_commit="a" * 40, source_path="f.bin",
        source_git_blob_sha1=hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest(),
        source_url=url, expected_size=len(data), sha256=hashlib.sha256(data).hexdigest(),
        license="test", license_basis="test",
    )


class Response(io.BytesIO):
    def __init__(self, data: bytes, final_url: str = "https://cdn.example/f.bin"):
        super().__init__(data); self.final_url = final_url
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def geturl(self): return self.final_url


def test_acquire_fixture_downloads_verifies_and_reuses(tmp_path, monkeypatch):
    data=b"fixture-data"; spec=_spec(data)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response(data))
    p, downloaded, reused, final=acquire_qualification_fixture(spec,tmp_path)
    assert p.read_bytes()==data and downloaded and not reused and final.startswith("https://")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    p2, downloaded2, reused2, final2=acquire_qualification_fixture(spec,tmp_path)
    assert p2==p and not downloaded2 and reused2 and final2 is None


def test_acquire_fixture_conflicting_existing_bytes_fail_without_overwrite(tmp_path):
    spec=_spec(b"good"); p=tmp_path/spec.filename; p.write_bytes(b"evil")
    with pytest.raises(QualificationFixtureError): acquire_qualification_fixture(spec,tmp_path)
    assert p.read_bytes()==b"evil"


def test_acquire_fixture_rejects_insecure_source_or_redirect(tmp_path, monkeypatch):
    with pytest.raises(QualificationFixtureError, match="HTTPS"):
        acquire_qualification_fixture(_spec(b"x",url="http://example.invalid/f"),tmp_path)
    spec=_spec(b"x")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response(b"x","http://mirror.invalid/f"))
    with pytest.raises(QualificationFixtureError, match="HTTPS"):
        acquire_qualification_fixture(spec,tmp_path)


def test_acquire_fixture_bad_download_never_commits_or_leaves_staging(tmp_path, monkeypatch):
    spec=_spec(b"good")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response(b"baad"))
    with pytest.raises(QualificationFixtureError): acquire_qualification_fixture(spec,tmp_path)
    assert not (tmp_path/spec.filename).exists()
    assert not list(tmp_path.glob(".gpa-qualification-fixture-*"))
