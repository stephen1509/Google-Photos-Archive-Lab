from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gpa.__main__ import main
from gpa.raw_corpus import (
    RAW_CORPUS_MANIFEST_SCHEMA,
    RAW_CORPUS_QUALIFICATION_SCHEMA,
    RawCorpusPolicy,
    load_raw_corpus_manifest,
    qualify_raw_corpus,
    write_raw_corpus_qualification_report,
)
from gpa.raw_validation import RawProbeResult


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_manifest(path: Path, rows: list[dict], *, corpus_id: str = "test-corpus") -> Path:
    obj = {
        "schema": RAW_CORPUS_MANIFEST_SCHEMA,
        "corpus_id": corpus_id,
        "source_catalog": "test source",
        "source_base_url": "https://example.invalid/data/",
        "source_index_url": "https://example.invalid/filelist.sha256",
        "catalog_snapshot_date": "2026-08-24",
        "rights_note": "test media only",
        "samples": rows,
    }
    path.write_text(json.dumps(obj, sort_keys=True), encoding="utf-8")
    return path


def _row(sample_id: str, brand: str, family: str, rel: str, data: bytes) -> dict:
    return {
        "sample_id": sample_id,
        "brand": brand,
        "model": f"{brand} model",
        "format_family": family,
        "relative_path": rel,
        "sha256": _sha(data),
    }


def _good_probe(*_args, **_kwargs) -> RawProbeResult:
    return RawProbeResult(
        "passed",
        rawpy_version="0.27.0-test",
        libraw_version="0.22.1",
        validator_sha256="1" * 64,
        detail="decoded",
        decoded_width=10,
        decoded_height=8,
        decoded_channels=3,
    )


def test_canonical_public_manifest_has_exact_diverse_pinned_samples():
    p = Path(__file__).resolve().parents[1] / "qualification" / "raw_corpus_manifest_v1.json"
    m, digest = load_raw_corpus_manifest(p)
    assert len(m.samples) == 12
    assert len({x.brand.casefold() for x in m.samples}) == 11
    assert len({x.format_family for x in m.samples}) == 12
    assert len(digest) == 64
    by_id = {x.sample_id: x for x in m.samples}
    assert by_id["canon-eos-5d-mark-iii-cr2"].sha256 == "ec069b178b9383d80c72f8cdc1b79bd54ca451e7c20793b109d8ad73bef6bdf6"
    assert by_id["nikon-d70-nef"].sha256 == "dd6405aeb33b0cd5bf66c98ba98ccbb478a765450cfd130810e470dab8d1f4b4"
    assert by_id["apple-iphone-12-pro-dng"].sha256 == "e91e77a4533ed7cce551d83330676ea5c47dd5e55fb38adda7819366afdbdfc2"
    assert m.source_base_url.startswith("https://") and m.source_index_url.startswith("https://")
    assert "does not bundle" in m.rights_note


def test_manifest_rejects_extension_family_mismatch_and_path_traversal(tmp_path):
    data = b"raw"
    bad = _row("x", "A", "NEF", "../escape.NEF", data)
    p = _write_manifest(tmp_path / "bad.json", [bad])
    with pytest.raises(ValueError, match="unsafe relative_path"):
        load_raw_corpus_manifest(p)

    bad2 = _row("x", "A", "CR2", "A/x.NEF", data)
    p2 = _write_manifest(tmp_path / "bad2.json", [bad2])
    with pytest.raises(ValueError, match="does not match"):
        load_raw_corpus_manifest(p2)


def test_exact_hash_diverse_corpus_passes_and_binds_one_decoder(tmp_path, monkeypatch):
    root = tmp_path / "samples"; root.mkdir()
    rows = []
    specs = [("a","Canon","CR2"),("b","Nikon","NEF"),("c","Sony","ARW")]
    for sid, brand, fam in specs:
        data = ("DATA-" + sid).encode(); rel = f"{brand}/{sid}.{fam}"
        f = root / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(data)
        rows.append(_row(sid, brand, fam, rel, data))
    manifest = _write_manifest(tmp_path / "manifest.json", rows)
    monkeypatch.setattr("gpa.raw_corpus.probe_raw_isolated", _good_probe)
    r = qualify_raw_corpus(manifest, root, policy=RawCorpusPolicy(3, 3, 3))
    assert r.schema == RAW_CORPUS_QUALIFICATION_SCHEMA
    assert r.passed is True
    assert all(r.checks.values())
    assert r.decoder_identity == {
        "rawpy_version": "0.27.0-test", "libraw_version": "0.22.1", "validator_sha256": "1" * 64
    }
    assert all(x.hash_verified and x.bytes_unchanged and x.passed for x in r.samples)


def test_hash_mismatch_fails_before_decoder_probe(tmp_path, monkeypatch):
    root = tmp_path / "samples"; (root / "Canon").mkdir(parents=True)
    f = root / "Canon/x.CR2"; f.write_bytes(b"wrong")
    row = _row("x", "Canon", "CR2", "Canon/x.CR2", b"expected")
    manifest = _write_manifest(tmp_path / "manifest.json", [row])
    calls = []
    monkeypatch.setattr("gpa.raw_corpus.probe_raw_isolated", lambda *a, **k: calls.append(1) or _good_probe())
    r = qualify_raw_corpus(manifest, root, policy=RawCorpusPolicy(1, 1, 1))
    assert not r.passed and calls == []
    assert r.samples[0].hash_verified is False
    assert "mismatch" in " ".join(r.samples[0].errors).lower()


def test_missing_sample_and_insufficient_diversity_fail_closed(tmp_path):
    root = tmp_path / "samples"; root.mkdir()
    data = b"x"
    manifest = _write_manifest(tmp_path / "manifest.json", [_row("x", "Canon", "CR2", "Canon/x.CR2", data)])
    r = qualify_raw_corpus(manifest, root, policy=RawCorpusPolicy(2, 2, 2))
    assert not r.passed
    assert r.checks["diversity_sufficient"] is False
    assert r.samples[0].present is False
    assert "missing" in " ".join(r.samples[0].errors).lower()


def test_symlinked_sample_is_rejected_without_following_it(tmp_path, monkeypatch):
    root = tmp_path / "samples"; root.mkdir()
    outside = tmp_path / "outside.NEF"; outside.write_bytes(b"outside")
    link = root / "Nikon" / "x.NEF"; link.parent.mkdir()
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    row = _row("x", "Nikon", "NEF", "Nikon/x.NEF", b"outside")
    manifest = _write_manifest(tmp_path / "manifest.json", [row])
    calls = []
    monkeypatch.setattr("gpa.raw_corpus.probe_raw_isolated", lambda *a, **k: calls.append(1) or _good_probe())
    r = qualify_raw_corpus(manifest, root, policy=RawCorpusPolicy(1, 1, 1))
    assert not r.passed and calls == []
    assert r.samples[0].path_safe is False
    assert "symlink" in " ".join(r.samples[0].errors).lower()


def test_decoder_unavailable_and_decoder_identity_drift_fail(tmp_path, monkeypatch):
    root = tmp_path / "samples"; root.mkdir()
    rows = []
    for sid, brand, fam in [("a","Canon","CR2"),("b","Nikon","NEF")]:
        data = sid.encode(); rel=f"{brand}/{sid}.{fam}"
        p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        rows.append(_row(sid,brand,fam,rel,data))
    manifest=_write_manifest(tmp_path/"manifest.json",rows)

    monkeypatch.setattr("gpa.raw_corpus.probe_raw_isolated", lambda *a, **k: RawProbeResult("unavailable",detail="missing rawpy"))
    r=qualify_raw_corpus(manifest,root,policy=RawCorpusPolicy(2,2,2))
    assert not r.passed and r.checks["one_exact_decoder_build"] is False

    def drift(path, **_kwargs):
        suffix = Path(path).suffix.casefold()
        return RawProbeResult("passed",rawpy_version="0.27.0",libraw_version="0.22.1",validator_sha256=("1" if suffix==".cr2" else "2")*64,detail="ok",decoded_width=2,decoded_height=2,decoded_channels=3)
    monkeypatch.setattr("gpa.raw_corpus.probe_raw_isolated", drift)
    r2=qualify_raw_corpus(manifest,root,policy=RawCorpusPolicy(2,2,2))
    assert not r2.passed and r2.checks["all_samples_passed"] is True
    assert r2.checks["one_exact_decoder_build"] is False


def test_source_mutation_during_probe_is_detected(tmp_path, monkeypatch):
    root=tmp_path/"samples";root.mkdir();data=b"original";p=root/"Fuji/x.RAF";p.parent.mkdir();p.write_bytes(data)
    manifest=_write_manifest(tmp_path/"manifest.json",[_row("x","Fujifilm","RAF","Fuji/x.RAF",data)])
    def mutate(path, **_kwargs):
        Path(path).write_bytes(b"changed")
        return _good_probe()
    monkeypatch.setattr("gpa.raw_corpus.probe_raw_isolated",mutate)
    r=qualify_raw_corpus(manifest,root,policy=RawCorpusPolicy(1,1,1))
    assert not r.passed and r.samples[0].bytes_unchanged is False
    assert "changed" in " ".join(r.samples[0].errors).lower()


def test_report_writer_is_no_overwrite(tmp_path, monkeypatch):
    root=tmp_path/"samples";root.mkdir();data=b"x";p=root/"A/x.DNG";p.parent.mkdir();p.write_bytes(data)
    manifest=_write_manifest(tmp_path/"manifest.json",[_row("x","A","DNG","A/x.DNG",data)])
    monkeypatch.setattr("gpa.raw_corpus.probe_raw_isolated",_good_probe)
    r=qualify_raw_corpus(manifest,root,policy=RawCorpusPolicy(1,1,1))
    out=tmp_path/"report.json";write_raw_corpus_qualification_report(out,r)
    assert json.loads(out.read_text())["passed"] is True
    with pytest.raises(FileExistsError):write_raw_corpus_qualification_report(out,r)


def test_cli_qualify_raw_corpus_uses_canonical_policy_and_writes_report(tmp_path, monkeypatch, capsys):
    root=tmp_path/"samples";root.mkdir();rows=[]
    families=["CR2","CR3","NEF","ARW","RAF","RW2","ORF","PEF","DNG","SRW"]
    brands=["Canon","Canon2","Nikon","Sony","Fuji","Panasonic","Olympus","Pentax","Apple","Samsung"]
    for i,(fam,brand) in enumerate(zip(families,brands)):
        data=f"raw-{i}".encode();rel=f"{brand}/s{i}.{fam}";p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        rows.append(_row(f"s{i}",brand,fam,rel,data))
    manifest=_write_manifest(tmp_path/"manifest.json",rows)
    monkeypatch.setattr("gpa.raw_corpus.probe_raw_isolated",_good_probe)
    report=tmp_path/"report.json"
    rc=main(["qualify-raw-corpus","--manifest",str(manifest),"--samples-root",str(root),"--report",str(report),"--timeout","2"])
    assert rc==0
    obj=json.loads(report.read_text())
    assert obj["passed"] is True and obj["sample_count"]==10
    stdout=json.loads(capsys.readouterr().out)
    assert stdout["schema"]==RAW_CORPUS_QUALIFICATION_SCHEMA
