from __future__ import annotations

"""One-command Windows core qualification for GPA's real-machine evidence gate.

This orchestrator deliberately combines only evidence lanes that can be verified
without authorizing metadata writes: Windows platform/runtime identity, physical
storage independence, and exact-byte representative Camera RAW corpus decoding.
ExifTool writer promotion and Apple Photos acceptance remain separate gates.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import platform
import struct
import sys
from typing import Any

from .raw_corpus import fetch_raw_corpus, qualify_raw_corpus
from .storage_identity import assess_storage_independence
from .transactions import write_json_new

WINDOWS_CORE_QUALIFICATION_SCHEMA = "gpa.windows-core-qualification.v1"


@dataclass
class WindowsCoreQualificationReport:
    schema: str = WINDOWS_CORE_QUALIFICATION_SCHEMA
    created_utc: str = ""
    platform: dict[str, Any] = field(default_factory=dict)
    primary_path: str = ""
    backup_path: str = ""
    raw_manifest_path: str = ""
    raw_samples_root: str = ""
    raw_fetch: dict[str, Any] | None = None
    storage_assessment: dict[str, Any] | None = None
    raw_corpus_qualification: dict[str, Any] | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    passed: bool = False
    production_write_approved: bool = False
    google_retirement_approved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _regular_file_sha256(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _platform_evidence() -> dict[str, Any]:
    exe = Path(sys.executable)
    bits = struct.calcsize("P") * 8
    machine = platform.machine()
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": machine,
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": str(exe),
        "python_executable_sha256": _regular_file_sha256(exe),
        "python_pointer_bits": bits,
        "windows_x64_eligible": bool(
            platform.system() == "Windows"
            and bits == 64
            and machine.casefold() in {"amd64", "x86_64"}
        ),
    }


def qualify_windows_core(
    primary_path: str | Path,
    backup_path: str | Path,
    raw_manifest_path: str | Path,
    raw_samples_root: str | Path,
    *,
    fetch_raw: bool = False,
    timeout_seconds: float = 120.0,
) -> WindowsCoreQualificationReport:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    primary = Path(primary_path)
    backup = Path(backup_path)
    manifest = Path(raw_manifest_path)
    samples = Path(raw_samples_root)
    report = WindowsCoreQualificationReport(
        created_utc=datetime.now(timezone.utc).isoformat(),
        platform=_platform_evidence(),
        primary_path=str(primary),
        backup_path=str(backup),
        raw_manifest_path=str(manifest),
        raw_samples_root=str(samples),
    )

    checks: dict[str, bool] = {
        "platform_is_windows": report.platform.get("system") == "Windows",
        "windows_x64_eligible": report.platform.get("windows_x64_eligible") is True,
        "python_executable_fingerprinted": isinstance(report.platform.get("python_executable_sha256"), str)
        and len(str(report.platform.get("python_executable_sha256"))) == 64,
        "primary_directory_exists": primary.is_dir() and not primary.is_symlink(),
        "backup_directory_exists": backup.is_dir() and not backup.is_symlink(),
        "raw_manifest_regular_file": manifest.is_file() and not manifest.is_symlink(),
    }

    try:
        storage = assess_storage_independence(primary, backup)
        report.storage_assessment = storage.to_dict()
        checks["storage_separate_physical_device_proven"] = bool(storage.separate_physical_device_proven)
        checks["storage_separate_physical_media_confirmed"] = bool(storage.separate_physical_media_confirmed)
        checks["storage_machine_evidence_source"] = (
            storage.independence_evidence_source == "windows_disk_extents_and_storage_device_descriptor"
        )
        checks["storage_no_identity_conflict"] = not bool(storage.physical_identity_conflict)
    except Exception as e:
        report.errors.append(f"storage qualification failed: {e}")
        checks.update({
            "storage_separate_physical_device_proven": False,
            "storage_separate_physical_media_confirmed": False,
            "storage_machine_evidence_source": False,
            "storage_no_identity_conflict": False,
        })

    if fetch_raw:
        try:
            fetched = fetch_raw_corpus(manifest, samples, timeout_seconds=timeout_seconds)
            report.raw_fetch = fetched.to_dict()
            checks["raw_fetch_passed"] = bool(fetched.passed)
            if not fetched.passed:
                report.errors.append("RAW corpus fetch did not pass")
        except Exception as e:
            report.errors.append(f"RAW corpus fetch failed: {e}")
            checks["raw_fetch_passed"] = False
    else:
        checks["raw_fetch_passed"] = True  # Not required when exact local bytes already exist.

    try:
        raw = qualify_raw_corpus(manifest, samples, timeout_seconds=timeout_seconds)
        report.raw_corpus_qualification = raw.to_dict()
        checks["raw_corpus_passed"] = bool(raw.passed)
        checks["raw_decoder_identity_consistent"] = bool(raw.decoder_identity_consistent)
        ident = raw.decoder_identity or {}
        checks["raw_decoder_fingerprinted"] = (
            isinstance(ident.get("validator_sha256"), str) and len(str(ident.get("validator_sha256"))) == 64
        )
        checks["raw_source_bytes_unchanged"] = raw.checks.get("all_bytes_unchanged") is True
        if not raw.passed:
            report.errors.append("RAW corpus qualification did not pass")
    except Exception as e:
        report.errors.append(f"RAW corpus qualification failed: {e}")
        checks.update({
            "raw_corpus_passed": False,
            "raw_decoder_identity_consistent": False,
            "raw_decoder_fingerprinted": False,
            "raw_source_bytes_unchanged": False,
        })

    report.checks = checks
    report.passed = all(checks.values()) and not report.errors
    # This report can never authorize writer use or Google retirement by itself.
    report.production_write_approved = False
    report.google_retirement_approved = False
    return report


def write_windows_core_qualification_report(path: str | Path, report: WindowsCoreQualificationReport) -> Path:
    path = Path(path)
    write_json_new(path, report.to_dict())
    return path
