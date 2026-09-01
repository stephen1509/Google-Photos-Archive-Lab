from __future__ import annotations

"""Consolidated Windows writer-evidence review session.

This layer starts from the v39 Windows release gate and, only when that exact
session passes, exercises the existing per-format and composite-relationship
round-trip harnesses against the same admitted ExifTool executable/distribution.
It produces review evidence only.  It never mutates the production approval
allow-lists and never authorizes Google retirement.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .exiftool_distribution import get_distribution_spec
from .format_evidence import build_format_evidence_bundle
from .format_roundtrip_qualification import qualify_exiftool_format
from .qualification_fixtures import HEIC_FIXTURE, acquire_qualification_fixture, verify_qualification_fixture
from .relationship_evidence import build_relationship_evidence_bundle
from .relationship_roundtrip_qualification import qualify_exiftool_live_photo, qualify_exiftool_motion_photo
from .transactions import sha256_file, write_json_new
from .windows_release_qualification import qualify_windows_release

WINDOWS_WRITER_QUALIFICATION_SCHEMA = "gpa.windows-writer-qualification.v1"
DEFAULT_FORMAT_PROFILES = (
    "jpeg-single-v1",
    "png-single-v1",
    "mov-single-v1",
    "mp4-single-v1",
    "m4v-single-v1",
    "avif-single-v1",
    "heic-single-v1",
)


@dataclass
class WindowsWriterQualificationReport:
    schema: str = WINDOWS_WRITER_QUALIFICATION_SCHEMA
    created_utc: str = ""
    exiftool_candidate_id: str = ""
    windows_release: dict[str, Any] | None = None
    format_qualifications: dict[str, dict[str, Any]] = field(default_factory=dict)
    format_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationship_qualifications: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationship_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    ready_for_governance_review: bool = False
    production_write_approved: bool = False
    google_retirement_approved: bool = False

    @property
    def passed(self) -> bool:
        return self.ready_for_governance_review and not self.errors and all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


def _report_sha256(report: dict[str, Any]) -> str:
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _same_candidate(report: dict[str, Any], *, version: str, executable_sha256: str, distribution_sha256: str) -> bool:
    return (
        report.get("version") == version
        and report.get("executable_sha256") == executable_sha256
        and report.get("distribution_sha256") == distribution_sha256
    )


def qualify_windows_writer_review(
    primary_path: str | Path,
    backup_path: str | Path,
    raw_manifest_path: str | Path,
    raw_samples_root: str | Path,
    *,
    exiftool_candidate_id: str,
    exiftool_cache_dir: str | Path,
    exiftool_prepare_parent: str | Path,
    exiftool_workdir: str | Path,
    writer_workdir: str | Path,
    heic_fixture_cache: str | Path,
    fetch_raw: bool = False,
    fetch_exiftool: bool = False,
    fetch_heic_fixture: bool = False,
    format_profiles: tuple[str, ...] = DEFAULT_FORMAT_PROFILES,
    timeout_seconds: float = 120.0,
    exiftool_subprocess_timeout_seconds: float = 30.0,
) -> WindowsWriterQualificationReport:
    if timeout_seconds <= 0 or exiftool_subprocess_timeout_seconds <= 0:
        raise ValueError("qualification timeouts must be > 0")
    if not format_profiles:
        raise ValueError("at least one format profile is required")
    if len(set(format_profiles)) != len(format_profiles):
        raise ValueError("format profiles must be unique")
    unsupported = sorted(set(format_profiles) - set(DEFAULT_FORMAT_PROFILES))
    if unsupported:
        raise ValueError(f"unsupported Windows writer qualification profile(s): {', '.join(unsupported)}")

    out = WindowsWriterQualificationReport(
        created_utc=datetime.now(timezone.utc).isoformat(),
        exiftool_candidate_id=exiftool_candidate_id,
    )
    checks: dict[str, bool] = {}

    try:
        release = qualify_windows_release(
            primary_path,
            backup_path,
            raw_manifest_path,
            raw_samples_root,
            exiftool_candidate_id=exiftool_candidate_id,
            exiftool_cache_dir=exiftool_cache_dir,
            exiftool_prepare_parent=exiftool_prepare_parent,
            exiftool_workdir=exiftool_workdir,
            fetch_raw=fetch_raw,
            fetch_exiftool=fetch_exiftool,
            timeout_seconds=timeout_seconds,
            exiftool_subprocess_timeout_seconds=exiftool_subprocess_timeout_seconds,
        )
        release_dict = release.to_dict()
        out.windows_release = release_dict
        checks["windows_release_passed"] = bool(release.passed)
        checks["windows_release_not_production_approval"] = release.production_write_approved is False
        checks["windows_release_not_retirement_approval"] = release.google_retirement_approved is False
        if not release.passed:
            out.errors.append("Windows release qualification did not pass")
            out.checks = checks
            return out
    except Exception as e:
        out.errors.append(f"Windows release qualification failed: {e}")
        checks.update({
            "windows_release_passed": False,
            "windows_release_not_production_approval": False,
            "windows_release_not_retirement_approval": False,
        })
        out.checks = checks
        return out

    try:
        admission = dict((out.windows_release or {}).get("exiftool_admission") or {})
        base_evidence = dict((out.windows_release or {}).get("exiftool_base_evidence") or {})
        spec = get_distribution_spec(exiftool_candidate_id)
        executable = Path(str(admission.get("executable_path") or ""))
        distribution_root = Path(str(admission.get("prepared_distribution_root") or ""))
        archive = Path(exiftool_cache_dir) / spec.filename
        version = str(base_evidence.get("version") or "")
        exe_sha = str(base_evidence.get("executable_sha256") or "")
        dist_sha = str(base_evidence.get("distribution_sha256") or "")
        checks["base_evidence_review_ready"] = base_evidence.get("ready_for_promotion_review") is True
        checks["live_executable_matches_base"] = executable.is_file() and sha256_file(executable) == exe_sha
        checks["live_distribution_identity_present"] = len(dist_sha) == 64
        checks["live_archive_matches_pin"] = archive.is_file() and sha256_file(archive) == spec.sha256
        if not all(checks[k] for k in (
            "base_evidence_review_ready", "live_executable_matches_base", "live_distribution_identity_present", "live_archive_matches_pin"
        )):
            raise RuntimeError("live ExifTool/base evidence identity did not pass")

        fixture_cache = Path(heic_fixture_cache)
        heic_fixture = fixture_cache / HEIC_FIXTURE.filename
        if "heic-single-v1" in format_profiles:
            if fetch_heic_fixture:
                heic_fixture, _, _, _ = acquire_qualification_fixture(
                    HEIC_FIXTURE, fixture_cache, timeout_seconds=timeout_seconds
                )
            else:
                verify_qualification_fixture(heic_fixture, HEIC_FIXTURE)
            checks["heic_fixture_exact"] = True

        writer_root = Path(writer_workdir)
        writer_root.mkdir(parents=True, exist_ok=True)
        format_bundles: dict[str, dict[str, Any]] = {}
        for profile in format_profiles:
            fq = qualify_exiftool_format(
                executable,
                writer_root / "formats" / profile,
                format_profile_id=profile,
                distribution_root=distribution_root,
                qualification_fixture=(heic_fixture if profile == "heic-single-v1" else None),
                subprocess_timeout_seconds=exiftool_subprocess_timeout_seconds,
            )
            fq_dict = fq.to_dict()
            out.format_qualifications[profile] = fq_dict
            checks[f"format:{profile}:passed"] = bool(fq.passed) and not fq.errors
            checks[f"format:{profile}:candidate_matches"] = _same_candidate(
                fq_dict, version=version, executable_sha256=exe_sha, distribution_sha256=dist_sha
            )
            fe = build_format_evidence_bundle(
                base_evidence,
                fq_dict,
                base_evidence_report_sha256=_report_sha256(base_evidence),
                format_qualification_report_sha256=_report_sha256(fq_dict),
                live_archive_size_bytes=archive.stat().st_size,
                live_archive_sha256=sha256_file(archive),
                live_executable_sha256=sha256_file(executable),
                live_distribution_sha256=dist_sha,
            )
            fe_dict = fe.to_dict()
            out.format_evidence[profile] = fe_dict
            format_bundles[profile] = fe_dict
            checks[f"format:{profile}:review_ready"] = bool(fe.ready_for_format_promotion_review)
            checks[f"format:{profile}:not_auto_approved"] = fe.production_write_approved is False

        # Relationship lanes require their component format evidence to be part of
        # this same exact candidate session.
        relationship_specs = []
        for profile in ("jpeg-single-v1", "heic-single-v1", "avif-single-v1"):
            if profile in format_bundles:
                relationship_specs.append((f"motion:{profile}", "motion", profile))
        if "jpeg-single-v1" in format_bundles and "mov-single-v1" in format_bundles:
            relationship_specs.append(("live:jpeg+mov", "live", "jpeg-single-v1"))

        for key, kind, profile in relationship_specs:
            if kind == "motion":
                rq = qualify_exiftool_motion_photo(
                    executable,
                    writer_root / "relationships" / key.replace(":", "_"),
                    distribution_root=distribution_root,
                    format_profile_id=profile,
                    qualification_fixture=(heic_fixture if profile == "heic-single-v1" else None),
                    subprocess_timeout_seconds=exiftool_subprocess_timeout_seconds,
                )
                secondary = None
            else:
                rq = qualify_exiftool_live_photo(
                    executable,
                    writer_root / "relationships" / "live_jpeg_mov",
                    distribution_root=distribution_root,
                    subprocess_timeout_seconds=exiftool_subprocess_timeout_seconds,
                )
                secondary = format_bundles["mov-single-v1"]
            rq_dict = rq.to_dict()
            out.relationship_qualifications[key] = rq_dict
            checks[f"relationship:{key}:passed"] = bool(rq.passed) and not rq.errors
            checks[f"relationship:{key}:candidate_matches"] = _same_candidate(
                rq_dict, version=version, executable_sha256=exe_sha, distribution_sha256=dist_sha
            )
            primary_fe = format_bundles[profile]
            re = build_relationship_evidence_bundle(
                primary_fe,
                rq_dict,
                secondary_format_evidence=secondary,
                format_evidence_report_sha256=_report_sha256(primary_fe),
                secondary_format_evidence_report_sha256=(_report_sha256(secondary) if secondary is not None else None),
                relationship_qualification_report_sha256=_report_sha256(rq_dict),
                live_executable_sha256=sha256_file(executable),
                live_distribution_sha256=dist_sha,
            )
            re_dict = re.to_dict()
            out.relationship_evidence[key] = re_dict
            checks[f"relationship:{key}:review_ready"] = bool(re.ready_for_relationship_promotion_review)
            checks[f"relationship:{key}:not_auto_approved"] = re.production_write_approved is False
    except Exception as e:
        out.errors.append(f"Writer evidence lane failed: {e}")

    out.checks = checks
    out.ready_for_governance_review = bool(checks) and all(checks.values()) and not out.errors
    out.production_write_approved = False
    out.google_retirement_approved = False
    return out


def write_windows_writer_qualification_report(path: str | Path, report: WindowsWriterQualificationReport) -> Path:
    path = Path(path)
    write_json_new(path, report.to_dict())
    return path
