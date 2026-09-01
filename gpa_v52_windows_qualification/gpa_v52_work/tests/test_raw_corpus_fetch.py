from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gpa.__main__ import main
from gpa.raw_corpus import (
    RAW_CORPUS_FETCH_SCHEMA,
    RAW_CORPUS_MANIFEST_SCHEMA,
    _parse_sha256_index,
    fetch_raw_corpus,
    write_raw_corpus_fetch_report,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row(sample_id: str, rel: str, data: bytes) -> dict:
    family = Path(rel).suffix.lstrip(".").upper()
    return {
        "sample_id": sample_id,
        "brand": sample_id.split("-")[0].title(),
        "model": f"{sample_id} model",
        "format_family": family,
        "relative_path": rel,
        "sha256": _sha(data),
    }


def _manifest(path: Path, rows: list[dict]) -> Path:
    obj = {
        "schema": RAW_CORPUS_MANIFEST_SCHEMA,
        "corpus_id": "fetch-test-corpus",
        "source_catalog": "test source",
        "source_base_url": "https://example.invalid/data/",
        "source_index_url": "https://example.invalid/data/filelist.sha256",
        "catalog_snapshot_date": "2026-08-24",
        "rights_note": "test only",
        "samples": rows,
    }
    path.write_text(json.dumps(obj, sort_keys=True), encoding="utf-8")
    return path


def _index(rows: list[dict], *, overrides: dict[str, str] | None = None) -> bytes:
    overrides = overrides or {}
    return "".join(
        f"{overrides.get(r['relative_path'], r['sha256'])}  {r['relative_path']}\n" for r in rows
    ).encode()


class FakeResponse:
    def __init__(self, data: bytes, url: str):
        self.data = data
        self.url = url
        self.pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, size=-1):
        if self.pos >= len(self.data):
            return b""
        if size is None or size < 0:
            size = len(self.data) - self.pos
        out = self.data[self.pos:self.pos + size]
        self.pos += len(out)
        return out


class FakeOpener:
    def __init__(self, mapping: dict[str, tuple[bytes, str | None]]):
        self.mapping = mapping
        self.calls: list[str] = []

    def __call__(self, url: str, _timeout: float):
        self.calls.append(url)
        if url not in self.mapping:
            raise AssertionError(f"unexpected network request {url}")
        data, final = self.mapping[url]
        return FakeResponse(data, final or url)


def test_parse_raw_pixls_style_index_and_reject_malformed_or_conflicting_rows():
    a = b"alpha"; b = b"beta"
    text = f"{_sha(a)}  Canon/EOS 5D/x.CR2\n{_sha(b)} *Nikon/D70/y.NEF\n".encode()
    assert _parse_sha256_index(text) == {
        "Canon/EOS 5D/x.CR2": _sha(a),
        "Nikon/D70/y.NEF": _sha(b),
    }
    with pytest.raises(ValueError, match="malformed"):
        _parse_sha256_index(b"not-a-hash file.CR2\n")
    conflict = f"{_sha(a)}  A/x.CR2\n{_sha(b)}  A/x.CR2\n".encode()
    with pytest.raises(ValueError, match="conflicting"):
        _parse_sha256_index(conflict)


def test_successful_fetch_encodes_paths_hashes_before_commit_and_records_final_urls(tmp_path):
    d1=b"raw-one"; d2=b"raw-two"
    rows=[_row("canon", "Canon/EOS 5D/x one.CR2", d1), _row("nikon", "Nikon/D70/y.NEF", d2)]
    manifest=_manifest(tmp_path/"manifest.json", rows)
    index_url="https://example.invalid/data/filelist.sha256"
    u1="https://example.invalid/data/Canon/EOS%205D/x%20one.CR2"
    u2="https://example.invalid/data/Nikon/D70/y.NEF"
    opener=FakeOpener({
        index_url: (_index(rows), "https://cdn.example.invalid/filelist.sha256"),
        u1: (d1, "https://cdn.example.invalid/files/x.CR2"),
        u2: (d2, None),
    })
    root=tmp_path/"samples"
    r=fetch_raw_corpus(manifest,root,timeout_seconds=3,opener=opener)
    assert r.schema == RAW_CORPUS_FETCH_SCHEMA and r.passed
    assert r.source_index_verified and r.source_index_final_url == "https://cdn.example.invalid/filelist.sha256"
    assert r.downloaded == 2 and r.reused == 0 and r.failed == 0
    assert (root/"Canon/EOS 5D/x one.CR2").read_bytes() == d1
    assert (root/"Nikon/D70/y.NEF").read_bytes() == d2
    assert r.samples[0].final_url == "https://cdn.example.invalid/files/x.CR2"
    assert opener.calls == [index_url,u1,u2]
    assert not list(root.rglob(".gpa-raw-corpus-*"))


def test_second_fetch_reuses_exact_files_but_rechecks_live_source_index(tmp_path):
    data=b"raw"; rows=[_row("canon","Canon/x.CR2",data)]
    manifest=_manifest(tmp_path/"manifest.json",rows)
    root=tmp_path/"samples";(root/"Canon").mkdir(parents=True);(root/"Canon/x.CR2").write_bytes(data)
    index_url="https://example.invalid/data/filelist.sha256"
    opener=FakeOpener({index_url:(_index(rows),None)})
    r=fetch_raw_corpus(manifest,root,opener=opener)
    assert r.passed and r.reused==1 and r.downloaded==0
    assert opener.calls == [index_url]
    assert r.samples[0].status == "reused" and r.samples[0].bytes_downloaded == 0


def test_preexisting_mismatch_fails_closed_and_preserves_existing_bytes(tmp_path):
    expected=b"expected"; existing=b"do-not-touch"
    rows=[_row("canon","Canon/x.CR2",expected)]
    manifest=_manifest(tmp_path/"manifest.json",rows)
    root=tmp_path/"samples";(root/"Canon").mkdir(parents=True);dest=root/"Canon/x.CR2";dest.write_bytes(existing)
    index_url="https://example.invalid/data/filelist.sha256"
    opener=FakeOpener({index_url:(_index(rows),None)})
    r=fetch_raw_corpus(manifest,root,opener=opener)
    assert not r.passed and r.failed==1 and dest.read_bytes()==existing
    assert "existing destination SHA-256 mismatch" in (r.samples[0].error or "")
    assert opener.calls == [index_url]


def test_source_index_mismatch_blocks_all_sample_download_requests(tmp_path):
    data=b"expected"; rows=[_row("canon","Canon/x.CR2",data),_row("nikon","Nikon/y.NEF",b"two")]
    manifest=_manifest(tmp_path/"manifest.json",rows)
    index_url="https://example.invalid/data/filelist.sha256"
    bad="0"*64
    opener=FakeOpener({index_url:(_index(rows,overrides={rows[0]["relative_path"]:bad}),None)})
    r=fetch_raw_corpus(manifest,tmp_path/"samples",opener=opener)
    assert not r.passed and not r.source_index_verified and r.failed==2
    assert opener.calls == [index_url]
    assert all(x.source_url is None for x in r.samples)
    assert "source index verification failed" in " ".join(r.errors)


def test_download_hash_mismatch_cleans_temp_and_creates_no_destination(tmp_path):
    expected=b"expected"; wrong=b"wrong-download"
    rows=[_row("canon","Canon/x.CR2",expected)]
    manifest=_manifest(tmp_path/"manifest.json",rows)
    index_url="https://example.invalid/data/filelist.sha256"; sample_url="https://example.invalid/data/Canon/x.CR2"
    opener=FakeOpener({index_url:(_index(rows),None),sample_url:(wrong,None)})
    root=tmp_path/"samples"
    r=fetch_raw_corpus(manifest,root,opener=opener)
    assert not r.passed and r.failed==1
    assert not (root/"Canon/x.CR2").exists()
    assert not list(root.rglob(".gpa-raw-corpus-*"))
    assert "downloaded SHA-256 mismatch" in (r.samples[0].error or "")


def test_non_https_redirect_fails_closed_for_index_and_sample(tmp_path):
    data=b"raw"; rows=[_row("canon","Canon/x.CR2",data)]
    manifest=_manifest(tmp_path/"manifest.json",rows)
    index_url="https://example.invalid/data/filelist.sha256"; sample_url="https://example.invalid/data/Canon/x.CR2"
    index_bad=FakeOpener({index_url:(_index(rows),"http://example.invalid/filelist.sha256")})
    r=fetch_raw_corpus(manifest,tmp_path/"one",opener=index_bad)
    assert not r.passed and index_bad.calls == [index_url]
    assert "non-HTTPS" in " ".join(r.errors)

    sample_bad=FakeOpener({index_url:(_index(rows),None),sample_url:(data,"http://example.invalid/x.CR2")})
    r2=fetch_raw_corpus(manifest,tmp_path/"two",opener=sample_bad)
    assert not r2.passed and r2.failed==1
    assert not (tmp_path/"two/Canon/x.CR2").exists()
    assert "non-HTTPS" in (r2.samples[0].error or "")


def test_symlinked_samples_root_and_parent_fail_untouched(tmp_path):
    data=b"raw"; rows=[_row("canon","Canon/x.CR2",data)]
    manifest=_manifest(tmp_path/"manifest.json",rows); index_url="https://example.invalid/data/filelist.sha256"
    try:
        real=tmp_path/"real"; real.mkdir(); rootlink=tmp_path/"rootlink"; rootlink.symlink_to(real, target_is_directory=True)
    except (OSError,NotImplementedError):
        pytest.skip("symlinks unavailable")
    opener=FakeOpener({index_url:(_index(rows),None)})
    r=fetch_raw_corpus(manifest,rootlink,opener=opener)
    assert not r.passed and not (real/"Canon/x.CR2").exists()
    assert "samples root must not be a symlink" in (r.samples[0].error or "")

    root=tmp_path/"samples"; root.mkdir(); outside=tmp_path/"outside";outside.mkdir();(root/"Canon").symlink_to(outside,target_is_directory=True)
    opener2=FakeOpener({index_url:(_index(rows),None)})
    r2=fetch_raw_corpus(manifest,root,opener=opener2)
    assert not r2.passed and not (outside/"x.CR2").exists()
    assert "symlink component" in (r2.samples[0].error or "")


def test_fetch_report_writer_is_no_overwrite(tmp_path):
    data=b"raw"; rows=[_row("canon","Canon/x.CR2",data)]
    manifest=_manifest(tmp_path/"manifest.json",rows)
    index_url="https://example.invalid/data/filelist.sha256"; sample_url="https://example.invalid/data/Canon/x.CR2"
    opener=FakeOpener({index_url:(_index(rows),None),sample_url:(data,None)})
    r=fetch_raw_corpus(manifest,tmp_path/"samples",opener=opener)
    out=tmp_path/"report.json";write_raw_corpus_fetch_report(out,r)
    assert json.loads(out.read_text())['passed'] is True
    with pytest.raises(FileExistsError):
        write_raw_corpus_fetch_report(out,r)


def test_cli_fetch_raw_corpus_writes_machine_report_and_uses_no_overwrite(tmp_path, monkeypatch, capsys):
    data=b"raw"; rows=[_row("canon","Canon/x.CR2",data)]
    manifest=_manifest(tmp_path/"manifest.json",rows)
    index_url="https://example.invalid/data/filelist.sha256"; sample_url="https://example.invalid/data/Canon/x.CR2"
    opener=FakeOpener({index_url:(_index(rows),None),sample_url:(data,None)})
    monkeypatch.setattr("gpa.raw_corpus._open_https", opener)
    report=tmp_path/"report.json"
    rc=main(["fetch-raw-corpus","--manifest",str(manifest),"--samples-root",str(tmp_path/"samples"),"--report",str(report),"--timeout","3"])
    assert rc==0
    obj=json.loads(report.read_text()); assert obj['schema']==RAW_CORPUS_FETCH_SCHEMA and obj['passed']
    stdout=json.loads(capsys.readouterr().out); assert stdout['downloaded']==1
    rc2=main(["fetch-raw-corpus","--manifest",str(manifest),"--samples-root",str(tmp_path/"samples"),"--report",str(report),"--timeout","3"])
    assert rc2==2
    err=json.loads(capsys.readouterr().out); assert err['status']=='error'
