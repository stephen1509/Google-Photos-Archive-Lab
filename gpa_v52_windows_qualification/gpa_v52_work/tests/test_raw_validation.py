from __future__ import annotations

from pathlib import Path

from gpa.media_validation import validate_media
from gpa.raw_validation import RAW_PROBE_SCHEMA, probe_raw_isolated
from gpa.format_qualification import profile_for_suffix


def _install_fake_rawpy(tmp_path: Path, monkeypatch, *, mode: str = "good") -> Path:
    moddir = tmp_path / "fakepkg"
    moddir.mkdir()
    native = moddir / "_rawpy.fake"
    native.write_bytes(b"FAKE-RAWPY-NATIVE-BUILD-v1")
    if mode == "unavailable":
        source = "raise ImportError('rawpy intentionally unavailable for test')\n"
    else:
        source = f'''
from pathlib import Path
from types import SimpleNamespace
__version__ = "0.27.0-test"
libraw_version = (0, 22, 1)
flags = {{"OPENMP": True, "DNGLOSSYCODEC": True}}
_rawpy = SimpleNamespace(__file__={str(native)!r})

class _Sizes:
    raw_width = 20
    raw_height = 16
    width = 18
    height = 14

class _Decoded:
    shape = (7, 9, 3)

class _Raw:
    sizes = _Sizes()
    raw_type = "RawType.Flat"
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def postprocess(self, **kwargs):
        return _Decoded()

def imread(path):
    p = Path(path)
    data = p.read_bytes()
    mode = {mode!r}
    if mode == "decode-fail" or data.startswith(b"BAD"):
        raise RuntimeError("synthetic LibRaw decode failure")
    if mode == "mutate":
        p.write_bytes(data + b"-MUTATED")
    if mode == "hang":
        import time; time.sleep(5)
    return _Raw()
'''
    (moddir / "rawpy.py").write_text(source, encoding="utf-8")
    # The isolated child prepends GPA's src tree and then preserves PYTHONPATH.  A
    # single-module fake is therefore selected before site-packages in the child.
    monkeypatch.setenv("PYTHONPATH", str(moddir))
    return moddir


def test_raw_probe_is_explicitly_unavailable_when_decoder_cannot_import(tmp_path, monkeypatch):
    _install_fake_rawpy(tmp_path, monkeypatch, mode="unavailable")
    p = tmp_path / "a.cr3"; p.write_bytes(b"RAW")
    r = probe_raw_isolated(p, timeout_seconds=5)
    assert r.schema == RAW_PROBE_SCHEMA
    assert r.status == "unavailable"
    assert r.validator_sha256 is None
    assert "unavailable" in r.detail.lower()


def test_isolated_rawpy_libraw_decode_records_exact_provenance_and_dimensions(tmp_path, monkeypatch):
    _install_fake_rawpy(tmp_path, monkeypatch)
    p = tmp_path / "a.dng"; p.write_bytes(b"SYNTHETIC-RAW")
    r = probe_raw_isolated(p, timeout_seconds=5)
    assert r.status == "passed"
    assert r.rawpy_version == "0.27.0-test"
    assert r.libraw_version == "0.22.1"
    assert r.validator_sha256 and len(r.validator_sha256) == 64
    assert (r.raw_width, r.raw_height) == (20, 16)
    assert (r.visible_width, r.visible_height) == (18, 14)
    assert (r.decoded_width, r.decoded_height, r.decoded_channels) == (9, 7, 3)
    assert r.feature_flags == {"DNGLOSSYCODEC": True, "OPENMP": True}


def test_validate_media_raw_pass_is_bound_to_media_hash_and_decoder_build(tmp_path, monkeypatch):
    _install_fake_rawpy(tmp_path, monkeypatch)
    p = tmp_path / "a.nef"; p.write_bytes(b"SYNTHETIC-RAW")
    r = validate_media(p)
    assert r.status == "passed"
    assert r.validator == "RAW rawpy/LibRaw isolated decode"
    assert r.version == "rawpy 0.27.0-test; LibRaw 0.22.1"
    assert r.validator_sha256 and len(r.validator_sha256) == 64
    assert r.media_sha256 and len(r.media_sha256) == 64
    assert r.bytes_unchanged is True
    assert "decoded=9x7x3" in r.detail and "raw=20x16" in r.detail


def test_raw_decode_failure_is_failed_not_unavailable_when_decoder_exists(tmp_path, monkeypatch):
    _install_fake_rawpy(tmp_path, monkeypatch, mode="decode-fail")
    p = tmp_path / "bad.arw"; p.write_bytes(b"BAD-RAW")
    r = validate_media(p)
    assert r.status == "failed"
    assert r.version == "rawpy 0.27.0-test; LibRaw 0.22.1"
    assert r.validator_sha256 and len(r.validator_sha256) == 64
    assert r.bytes_unchanged is True
    assert "decode failed" in r.detail.lower()


def test_supposedly_read_only_raw_validator_mutation_is_detected(tmp_path, monkeypatch):
    _install_fake_rawpy(tmp_path, monkeypatch, mode="mutate")
    p = tmp_path / "a.raf"; p.write_bytes(b"SYNTHETIC-RAW")
    r = validate_media(p)
    assert r.status == "failed"
    assert r.bytes_unchanged is False
    assert "media bytes changed during read-only validation" in r.detail


def test_raw_probe_hang_is_bounded_and_failed(tmp_path, monkeypatch):
    _install_fake_rawpy(tmp_path, monkeypatch, mode="hang")
    p = tmp_path / "a.rw2"; p.write_bytes(b"SYNTHETIC-RAW")
    r = probe_raw_isolated(p, timeout_seconds=0.1)
    assert r.status == "failed"
    assert "timed out" in r.detail.lower()


def test_raw_writer_profile_remains_blocked_despite_read_only_decoder_lane():
    p = profile_for_suffix(".dng")
    assert p is not None and p.profile_id == "raw-single-v1"
    assert p.validator == "rawpy/LibRaw isolated read-only decode + exact media hash"
    assert p.payload_fingerprint is None
    assert p.qualification_harness is None
    assert p.qualification_ready is False
    assert "embedded writes" in (p.blocker or "")
