from __future__ import annotations

"""Isolated read-only Camera RAW validation using optional rawpy/LibRaw.

The parent archive process never imports the native RAW decoder.  Validation runs in a
fresh Python subprocess with a hard timeout so a decoder crash or hang cannot corrupt
archive state.  The probe records the exact rawpy Python/native implementation digest,
rawpy version, embedded LibRaw version/features and basic decoded-image dimensions.

This is a *read-only health validator*.  It is deliberately not a RAW metadata-writer
qualification and grants no permission to rewrite RAW files.
"""

from dataclasses import dataclass, asdict
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys

from .transactions import sha256_file

RAW_PROBE_SCHEMA = "gpa.rawpy-libraw-probe.v1"
RAW_VALIDATOR_NAME = "RAW rawpy/LibRaw isolated decode"
DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class RawProbeResult:
    status: str  # passed | failed | unavailable
    validator: str = RAW_VALIDATOR_NAME
    rawpy_version: str | None = None
    libraw_version: str | None = None
    validator_sha256: str | None = None
    detail: str = ""
    raw_width: int | None = None
    raw_height: int | None = None
    visible_width: int | None = None
    visible_height: int | None = None
    decoded_width: int | None = None
    decoded_height: int | None = None
    decoded_channels: int | None = None
    raw_type: str | None = None
    feature_flags: dict[str, bool] | None = None
    schema: str = RAW_PROBE_SCHEMA

    def to_dict(self) -> dict:
        return asdict(self)


def _candidate_files(rawpy_module) -> list[Path]:
    rows: list[Path] = []
    native = getattr(rawpy_module, "_rawpy", None)
    if native is None:
        try:
            import importlib
            native = importlib.import_module("rawpy._rawpy")
        except Exception:
            native = None
    for obj in (rawpy_module, native):
        f = getattr(obj, "__file__", None)
        if not f:
            continue
        try:
            p = Path(f).resolve()
        except Exception:
            continue
        if p.is_file():
            rows.append(p)
    # The native extension must be present for a real rawpy decoder.  Tests may use a
    # deterministic fake native artifact with a non-standard suffix.
    return sorted(set(rows), key=lambda p: str(p))


def _build_fingerprint(rawpy_module) -> str | None:
    files = _candidate_files(rawpy_module)
    if len(files) < 2:
        return None
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode("utf-8", errors="surrogateescape")); h.update(b"\0")
        h.update(str(p.stat().st_size).encode("ascii")); h.update(b"\0")
        h.update(sha256_file(p).encode("ascii")); h.update(b"\n")
    return h.hexdigest()


def _version_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        return ".".join(str(x) for x in value)
    text = str(value).strip()
    return text or None


def _safe_int(value) -> int | None:
    try:
        out = int(value)
        return out if out >= 0 else None
    except Exception:
        return None


def probe_raw_in_process(path: str | Path) -> RawProbeResult:
    """Run one RAW health probe in the current process.

    Production callers should use :func:`probe_raw_isolated` instead.  This function is
    the child-process implementation and is public mainly for deterministic testing.
    """
    path = Path(path)
    try:
        import rawpy  # type: ignore
    except Exception as e:
        return RawProbeResult("unavailable", detail=f"rawpy unavailable: {e}")

    rawpy_version = _version_text(getattr(rawpy, "__version__", None))
    libraw_version = _version_text(getattr(rawpy, "libraw_version", None))
    build_sha = _build_fingerprint(rawpy)
    flags_obj = getattr(rawpy, "flags", None)
    flags = None
    if isinstance(flags_obj, dict):
        flags = {str(k): bool(v) for k, v in sorted(flags_obj.items(), key=lambda kv: str(kv[0]))}

    if not rawpy_version or not libraw_version or not build_sha:
        return RawProbeResult(
            "failed", rawpy_version=rawpy_version, libraw_version=libraw_version,
            validator_sha256=build_sha, feature_flags=flags,
            detail="rawpy decoder provenance is incomplete; exact Python/native build and LibRaw version are required",
        )

    try:
        with rawpy.imread(str(path)) as raw:
            sizes = getattr(raw, "sizes", None)
            raw_width = _safe_int(getattr(sizes, "raw_width", None))
            raw_height = _safe_int(getattr(sizes, "raw_height", None))
            visible_width = _safe_int(getattr(sizes, "width", None))
            visible_height = _safe_int(getattr(sizes, "height", None))
            raw_type = _version_text(getattr(raw, "raw_type", None))

            # A successful postprocess proves that LibRaw can unpack and demosaic/decode
            # the source, not merely parse its header.  half_size bounds output memory
            # while still exercising the decode path.  No write is performed.
            rgb = raw.postprocess(
                half_size=True,
                use_camera_wb=False,
                use_auto_wb=False,
                no_auto_bright=True,
                output_bps=8,
            )
            shape = getattr(rgb, "shape", None)
            if not isinstance(shape, tuple) or len(shape) not in (2, 3):
                raise ValueError(f"decoded image has invalid shape {shape!r}")
            decoded_height = _safe_int(shape[0]); decoded_width = _safe_int(shape[1])
            decoded_channels = 1 if len(shape) == 2 else _safe_int(shape[2])
            if not decoded_width or not decoded_height or decoded_channels not in (1, 3, 4):
                raise ValueError(f"decoded image dimensions/channels are invalid: {shape!r}")
            if raw_width is not None and raw_width <= 0:
                raise ValueError("LibRaw reported non-positive raw width")
            if raw_height is not None and raw_height <= 0:
                raise ValueError("LibRaw reported non-positive raw height")
            if visible_width is not None and visible_width <= 0:
                raise ValueError("LibRaw reported non-positive visible width")
            if visible_height is not None and visible_height <= 0:
                raise ValueError("LibRaw reported non-positive visible height")

        return RawProbeResult(
            "passed", rawpy_version=rawpy_version, libraw_version=libraw_version,
            validator_sha256=build_sha, detail="RAW opened and decoded in isolated rawpy/LibRaw process",
            raw_width=raw_width, raw_height=raw_height, visible_width=visible_width,
            visible_height=visible_height, decoded_width=decoded_width,
            decoded_height=decoded_height, decoded_channels=decoded_channels,
            raw_type=raw_type, feature_flags=flags,
        )
    except Exception as e:
        return RawProbeResult(
            "failed", rawpy_version=rawpy_version, libraw_version=libraw_version,
            validator_sha256=build_sha, feature_flags=flags,
            detail=f"RAW decode failed: {type(e).__name__}: {e}",
        )


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    src_root = str(Path(__file__).resolve().parents[1])
    prior = env.get("PYTHONPATH", "")
    parts = [src_root] + ([prior] if prior else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def probe_raw_isolated(path: str | Path, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> RawProbeResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    path = Path(path).resolve()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "gpa.raw_validation", "--probe", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=timeout_seconds, check=False, env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return RawProbeResult("failed", detail=f"RAW validator timed out after {timeout_seconds:g}s")
    except Exception as e:
        return RawProbeResult("failed", detail=f"could not launch isolated RAW validator: {e}")

    lines = [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip()]
    if not lines:
        detail = (cp.stderr or "").strip()[-2000:] or f"RAW validator exited {cp.returncode} without a report"
        return RawProbeResult("failed", detail=detail)
    try:
        obj = json.loads(lines[-1])
        if obj.get("schema") != RAW_PROBE_SCHEMA:
            raise ValueError(f"unexpected schema {obj.get('schema')!r}")
        result = RawProbeResult(**obj)
    except Exception as e:
        detail = (cp.stderr or "").strip()[-1200:]
        return RawProbeResult("failed", detail=f"invalid RAW validator report: {e}" + (f"; stderr: {detail}" if detail else ""))

    expected_rc = 0 if result.status == "passed" else (3 if result.status == "unavailable" else 1)
    if cp.returncode != expected_rc:
        return RawProbeResult(
            "failed", rawpy_version=result.rawpy_version, libraw_version=result.libraw_version,
            validator_sha256=result.validator_sha256, feature_flags=result.feature_flags,
            detail=f"RAW validator status/exit-code mismatch: status={result.status} returncode={cp.returncode}",
        )
    return result


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--probe", type=Path, required=True)
    a = ap.parse_args(argv)
    result = probe_raw_in_process(a.probe)
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result.status == "passed" else (3 if result.status == "unavailable" else 1)


if __name__ == "__main__":
    raise SystemExit(_main())
