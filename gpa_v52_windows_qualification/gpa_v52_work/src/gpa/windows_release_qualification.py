from __future__ import annotations

"""Consolidated real-Windows evidence session for GPA.

This module intentionally stops before any per-format or relationship writer
promotion.  It binds the v38 Windows core gate to an exact pinned ExifTool
Windows distribution, the base JPEG qualification harness, and a review-ready
base evidence bundle.  Passing this report is evidence collection only and can
never authorize production metadata writes or Google retirement.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .exiftool_distribution import (
    acquire_distribution_archive,
    admit_distribution,
    get_distribution_spec,
    verify_distribution_archive,
)
from .exiftool_evidence import build_exiftool_evidence_bundle
from .exiftool_qualification import qualify_exiftool_candidate
from .transactions import sha256_file, write_json_new
from .windows_qualification import qualify_windows_core

WINDOWS_RELEASE_QUALIFICATION_SCHEMA = "gpa.windows-release-qualification.v1"


@dataclass
class WindowsReleaseQualificationReport:
    schema: str = WINDOWS_RELEASE_QUALIFICATION_SCHEMA
    created_utc: str = ""
    exiftool_candidate_id: str = ""
    windows_core: dict[str, Any] | None = None
    exiftool_admission: dict[str, Any] | None = None
    exiftool_base_qualification: dict[str, Any] | None = None
    exiftool_base_evidence: dict[str, Any] | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    passed: bool = False
    format_writer_qualification_complete: bool = False
    relationship_writer_qualification_complete: bool = False
    production_write_approved: bool = False
    google_retirement_approved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_report_sha256(report: dict[str, Any]) -> str:
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def qualify_windows_release(
    primary_path: str | Path,
    backup_path: str | Path,
    raw_manifest_path: str | Path,
    raw_samples_root: str | Path,
    *,
    exiftool_candidate_id: str,
    exiftool_cache_dir: str | Path,
    exiftool_prepare_parent: str | Path,
    exiftool_workdir: str | Path,
    fetch_raw: bool = False,
    fetch_exiftool: bool = False,
    timeout_seconds: float = 120.0,
    exiftool_subprocess_timeout_seconds: float = 30.0,
) -> WindowsReleaseQualificationReport:
    if timeout_seconds <= 0 or exiftool_subprocess_timeout_seconds <= 0:
        raise ValueError("qualification timeouts must be > 0")
    report = WindowsReleaseQualificationReport(
        created_utc=datetime.now(timezone.utc).isoformat(),
        exiftool_candidate_id=exiftool_candidate_id,
    )
    checks: dict[str, bool] = {}

    # First run the complete read-only Windows/storage/RAW gate.  The ExifTool
    # lane does not weaken or substitute for any of these requirements.
    try:
        core = qualify_windows_core(
            primary_path,
            backup_path,
            raw_manifest_path,
            raw_samples_root,
            fetch_raw=fetch_raw,
            timeout_seconds=timeout_seconds,
        )
        report.windows_core = core.to_dict()
        checks["windows_core_passed"] = bool(core.passed)
        checks["windows_core_never_authorizes_writes"] = core.production_write_approved is False
        checks["windows_core_never_authorizes_retirement"] = core.google_retirement_approved is False
        if not core.passed:
            report.errors.append("Windows core qualification did not pass")
    except Exception as e:
        report.errors.append(f"Windows core qualification failed: {e}")
        checks.update({
            "windows_core_passed": False,
            "windows_core_never_authorizes_writes": False,
            "windows_core_never_authorizes_retirement": False,
        })

    try:
        spec = get_distribution_spec(exiftool_candidate_id)
        checks["exiftool_candidate_is_windows_x64"] = spec.platform == "windows" and spec.architecture == "x64"
        cache = Path(exiftool_cache_dir)
        prepare_parent = Path(exiftool_prepare_parent)
        workdir = Path(exiftool_workdir)
        archive = cache / spec.filename
        downloaded = False
        reused = False
        final_url = None
        if fetch_exiftool:
            archive, downloaded, reused, final_url = acquire_distribution_archive(
                spec, cache, timeout_seconds=timeout_seconds
            )
        else:
            # No implicit network access.  Exact pre-existing bytes are required.
            verify_distribution_archive(archive, spec)
            reused = True

        admission = admit_distribution(
            spec,
            archive,
            prepare_parent,
            source_url=final_url,
            downloaded=downloaded,
            reused_existing=reused,
        )
        report.exiftool_admission = admission.to_dict()
        checks["exiftool_archive_exact"] = bool(admission.archive_verified) and not admission.errors
        checks["exiftool_distribution_prepared"] = admission.checks.get("distribution_prepared") is True
        checks["exiftool_executable_present"] = admission.checks.get("executable_present") is True
        checks["exiftool_version_matches_pin"] = admission.version == spec.version

        if not all(
            checks.get(k) is True
            for k in ("exiftool_archive_exact", "exiftool_distribution_prepared", "exiftool_executable_present")
        ):
            raise RuntimeError("ExifTool admission/preparation did not pass")

        executable = Path(str(admission.executable_path))
        distribution_root = Path(str(admission.prepared_distribution_root))
        qualification = qualify_exiftool_candidate(
            executable,
            workdir,
            distribution_root=distribution_root,
            subprocess_timeout_seconds=exiftool_subprocess_timeout_seconds,
        )
        report.exiftool_base_qualification = qualification.to_dict()
        checks["exiftool_base_qualification_passed"] = bool(qualification.passed) and not qualification.errors
        checks["exiftool_qualification_version_matches_pin"] = qualification.version == spec.version

        admission_dict = admission.to_dict()
        qualification_dict = qualification.to_dict()
        evidence = build_exiftool_evidence_bundle(
            admission_dict,
            qualification_dict,
            admission_report_sha256=_canonical_report_sha256(admission_dict),
            qualification_report_sha256=_canonical_report_sha256(qualification_dict),
            live_archive_size_bytes=archive.stat().st_size,
            live_archive_sha256=sha256_file(archive),
            live_executable_sha256=sha256_file(executable),
            live_distribution_sha256=admission.prepared_distribution_sha256,
        )
        report.exiftool_base_evidence = evidence.to_dict()
        checks["exiftool_base_evidence_review_ready"] = bool(evidence.ready_for_promotion_review)
        checks["exiftool_base_evidence_never_auto_approves"] = evidence.production_write_approved is False
    except Exception as e:
        report.errors.append(f"ExifTool evidence lane failed: {e}")
        for k in (
            "exiftool_candidate_is_windows_x64",
            "exiftool_archive_exact",
            "exiftool_distribution_prepared",
            "exiftool_executable_present",
            "exiftool_version_matches_pin",
            "exiftool_base_qualification_passed",
            "exiftool_qualification_version_matches_pin",
            "exiftool_base_evidence_review_ready",
            "exiftool_base_evidence_never_auto_approves",
        ):
            checks.setdefault(k, False)

    report.checks = checks
    report.passed = all(checks.values()) and not report.errors
    # Explicitly preserve the governance boundary: base evidence is insufficient
    # for per-format/relationship writer approval and insufficient for retirement.
    report.format_writer_qualification_complete = False
    report.relationship_writer_qualification_complete = False
    report.production_write_approved = False
    report.google_retirement_approved = False
    return report


def write_windows_release_qualification_report(
    path: str | Path, report: WindowsReleaseQualificationReport
) -> Path:
    path = Path(path)
    write_json_new(path, report.to_dict())
    return path
