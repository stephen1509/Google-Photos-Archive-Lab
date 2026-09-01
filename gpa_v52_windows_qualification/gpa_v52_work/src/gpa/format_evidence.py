from __future__ import annotations

"""Bind format-specific ExifTool qualification to exact build/distribution evidence.

This is an evidence-integrity layer only.  A review-ready bundle never grants
production write permission; promotion remains an explicit code/governance action.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .exiftool_distribution import distribution_tree_sha256, get_distribution_spec, verify_distribution_archive
from .exiftool_evidence import EVIDENCE_SCHEMA, expected_exiftool_evidence_bundle_id
from .format_qualification import FORMAT_PROFILES
from .format_roundtrip_qualification import FORMAT_QUALIFICATION_SCHEMA, expected_format_qualification_id
from .transactions import sha256_file, write_json_new
from .qualification_fixtures import FIXTURE_SCHEMA, fixture_for_profile

FORMAT_EVIDENCE_SCHEMA = 'gpa.exiftool-format-evidence-bundle.v1'


class FormatEvidenceError(RuntimeError):
    pass


@dataclass
class FormatEvidenceBundle:
    schema: str = FORMAT_EVIDENCE_SCHEMA
    created_utc: str = ''
    bundle_id: str = ''
    base_evidence_bundle_id: str | None = None
    candidate_id: str = ''
    version: str = ''
    executable_sha256: str | None = None
    distribution_sha256: str | None = None
    format_profile_id: str = ''
    format_qualification_id: str | None = None
    format_qualification_harness: str | None = None
    qualification_fixture_id: str | None = None
    qualification_fixture_sha256: str | None = None
    qualification_fixture_source_commit: str | None = None
    qualification_fixture_git_blob_sha1: str | None = None
    qualification_fixture_license: str | None = None
    validator: dict = field(default_factory=dict)
    base_evidence_report_sha256: str | None = None
    format_qualification_report_sha256: str | None = None
    live_archive_size_bytes: int | None = None
    live_archive_sha256: str | None = None
    live_executable_sha256: str | None = None
    live_distribution_sha256: str | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    ready_for_format_promotion_review: bool = False
    production_write_approved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _profile(profile_id: str):
    for p in FORMAT_PROFILES:
        if p.profile_id == profile_id:
            return p
    return None


def _canonical_id(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def expected_format_evidence_bundle_id(report: dict) -> str:
    """Recompute the canonical identity of a serialized format-evidence bundle."""
    return _canonical_id({
        'schema': str(report.get('schema') or ''),
        'base_evidence_bundle_id': report.get('base_evidence_bundle_id'),
        'candidate_id': str(report.get('candidate_id') or ''),
        'version': str(report.get('version') or ''),
        'executable_sha256': report.get('executable_sha256'),
        'distribution_sha256': report.get('distribution_sha256'),
        'format_profile_id': str(report.get('format_profile_id') or ''),
        'format_qualification_id': report.get('format_qualification_id'),
        'format_qualification_harness': report.get('format_qualification_harness'),
        'qualification_fixture_id': report.get('qualification_fixture_id'),
        'qualification_fixture_sha256': report.get('qualification_fixture_sha256'),
        'qualification_fixture_source_commit': report.get('qualification_fixture_source_commit'),
        'qualification_fixture_git_blob_sha1': report.get('qualification_fixture_git_blob_sha1'),
        'qualification_fixture_license': report.get('qualification_fixture_license'),
        'validator': dict(report.get('validator') or {}),
        'base_evidence_report_sha256': report.get('base_evidence_report_sha256'),
        'format_qualification_report_sha256': report.get('format_qualification_report_sha256'),
        'live_archive_size_bytes': report.get('live_archive_size_bytes'),
        'live_archive_sha256': report.get('live_archive_sha256'),
        'live_executable_sha256': report.get('live_executable_sha256'),
        'live_distribution_sha256': report.get('live_distribution_sha256'),
    })


def build_format_evidence_bundle(
    base_evidence: dict,
    format_qualification: dict,
    *,
    base_evidence_report_sha256: str | None = None,
    format_qualification_report_sha256: str | None = None,
    live_archive_size_bytes: int | None = None,
    live_archive_sha256: str | None = None,
    live_executable_sha256: str | None = None,
    live_distribution_sha256: str | None = None,
) -> FormatEvidenceBundle:
    candidate_id = str(base_evidence.get('candidate_id') or '')
    profile_id = str(format_qualification.get('format_profile_id') or '')
    fixture_admission = dict(format_qualification.get('fixture_admission') or {})
    bundle = FormatEvidenceBundle(
        created_utc=datetime.now(timezone.utc).isoformat(),
        base_evidence_bundle_id=base_evidence.get('bundle_id'),
        candidate_id=candidate_id,
        version=str(base_evidence.get('version') or ''),
        executable_sha256=base_evidence.get('executable_sha256'),
        distribution_sha256=base_evidence.get('distribution_sha256'),
        format_profile_id=profile_id,
        format_qualification_id=format_qualification.get('qualification_id'),
        format_qualification_harness=format_qualification.get('harness'),
        qualification_fixture_id=fixture_admission.get('fixture_id'),
        qualification_fixture_sha256=fixture_admission.get('observed_sha256'),
        qualification_fixture_source_commit=fixture_admission.get('source_commit'),
        qualification_fixture_git_blob_sha1=fixture_admission.get('observed_git_blob_sha1'),
        qualification_fixture_license=fixture_admission.get('license'),
        validator=dict(format_qualification.get('validator') or {}),
        base_evidence_report_sha256=base_evidence_report_sha256,
        format_qualification_report_sha256=format_qualification_report_sha256,
        live_archive_size_bytes=live_archive_size_bytes,
        live_archive_sha256=live_archive_sha256,
        live_executable_sha256=live_executable_sha256,
        live_distribution_sha256=live_distribution_sha256,
    )
    try:
        spec = get_distribution_spec(candidate_id)
    except Exception as e:
        bundle.errors.append(str(e))
        return bundle
    profile = _profile(profile_id)
    if profile is None:
        bundle.errors.append(f'unknown format profile: {profile_id or "<empty>"}')
        return bundle

    fq_checks = format_qualification.get('checks') or {}
    fixture_spec = fixture_for_profile(profile_id)
    if fixture_spec is None:
        fixture_requirement_satisfied = True
    else:
        fixture_requirement_satisfied = (
            fixture_admission.get('schema') == FIXTURE_SCHEMA
            and fixture_admission.get('fixture_id') == fixture_spec.fixture_id
            and fixture_admission.get('format_profile_id') == profile_id
            and fixture_admission.get('expected_sha256') == fixture_spec.sha256
            and fixture_admission.get('observed_sha256') == fixture_spec.sha256
            and fixture_admission.get('source_commit') == fixture_spec.source_commit
            and fixture_admission.get('source_git_blob_sha1') == fixture_spec.source_git_blob_sha1
            and fixture_admission.get('observed_git_blob_sha1') == fixture_spec.source_git_blob_sha1
            and fixture_admission.get('license') == fixture_spec.license
            and fixture_admission.get('exact_bytes_verified') is True
            and fixture_admission.get('source_git_blob_verified') is True
            and fixture_admission.get('production_write_approved') is False
        )
    checks = {
        'base_evidence_schema': base_evidence.get('schema') == EVIDENCE_SCHEMA,
        'base_evidence_review_ready': base_evidence.get('ready_for_promotion_review') is True,
        'base_evidence_checks_all_true': isinstance(base_evidence.get('checks'), dict) and bool(base_evidence.get('checks')) and all(v is True for v in base_evidence.get('checks', {}).values()),
        'base_evidence_not_production_approval': base_evidence.get('production_write_approved') is False,
        'base_evidence_bundle_id_present': isinstance(bundle.base_evidence_bundle_id, str) and bool(bundle.base_evidence_bundle_id),
        'base_evidence_bundle_id_valid': isinstance(bundle.base_evidence_bundle_id, str) and bundle.base_evidence_bundle_id == expected_exiftool_evidence_bundle_id(base_evidence),
        'format_qualification_schema': format_qualification.get('schema') == FORMAT_QUALIFICATION_SCHEMA,
        'format_qualification_passed': format_qualification.get('passed') is True and not format_qualification.get('errors'),
        'format_profile_known': True,
        'format_harness_registered': bool(profile.qualification_harness) and format_qualification.get('harness') == profile.qualification_harness,
        'qualification_fixture_requirement_satisfied': fixture_requirement_satisfied,
        'candidate_version_matches': format_qualification.get('version') == spec.version == base_evidence.get('version'),
        'executable_identity_matches': isinstance(bundle.executable_sha256, str) and len(bundle.executable_sha256) == 64 and format_qualification.get('executable_sha256') == bundle.executable_sha256,
        'distribution_identity_matches': isinstance(bundle.distribution_sha256, str) and len(bundle.distribution_sha256) == 64 and format_qualification.get('distribution_sha256') == bundle.distribution_sha256,
        'format_qualification_id_present': isinstance(bundle.format_qualification_id, str) and bool(bundle.format_qualification_id),
        'format_qualification_id_valid': isinstance(bundle.format_qualification_id, str) and bundle.format_qualification_id == expected_format_qualification_id(format_qualification),
        'validator_identified': fq_checks.get('validator_identified') is True and bool(bundle.validator.get('validator')) and bool(bundle.validator.get('version')),
        'validator_build_fingerprinted': fq_checks.get('validator_build_fingerprinted') is True and isinstance(bundle.validator.get('validator_sha256'), str) and len(bundle.validator.get('validator_sha256')) == 64,
        'media_payload_unchanged': fq_checks.get('media_payload_unchanged') is True,
        'output_decodable': fq_checks.get('output_decodable') is True,
        'metadata_readback_exact': fq_checks.get('readback_completed') is True and fq_checks.get('readback_description') is True and fq_checks.get('readback_xmp_date') is True,
        'base_evidence_report_bound': isinstance(base_evidence_report_sha256, str) and len(base_evidence_report_sha256) == 64,
        'format_qualification_report_bound': isinstance(format_qualification_report_sha256, str) and len(format_qualification_report_sha256) == 64,
        'live_archive_size_matches_pin': live_archive_size_bytes == spec.size_bytes,
        'live_archive_sha256_matches_pin': isinstance(live_archive_sha256, str) and live_archive_sha256.lower() == spec.sha256.lower(),
        'live_executable_identity_matches': isinstance(live_executable_sha256, str) and live_executable_sha256 == bundle.executable_sha256,
        'live_distribution_identity_matches': isinstance(live_distribution_sha256, str) and live_distribution_sha256 == bundle.distribution_sha256,
    }
    bundle.checks = checks
    bundle.ready_for_format_promotion_review = all(checks.values())
    bundle.production_write_approved = False
    bundle.bundle_id = _canonical_id({
        'schema': FORMAT_EVIDENCE_SCHEMA,
        'base_evidence_bundle_id': bundle.base_evidence_bundle_id,
        'candidate_id': candidate_id,
        'version': bundle.version,
        'executable_sha256': bundle.executable_sha256,
        'distribution_sha256': bundle.distribution_sha256,
        'format_profile_id': profile_id,
        'format_qualification_id': bundle.format_qualification_id,
        'format_qualification_harness': bundle.format_qualification_harness,
        'qualification_fixture_id': bundle.qualification_fixture_id,
        'qualification_fixture_sha256': bundle.qualification_fixture_sha256,
        'qualification_fixture_source_commit': bundle.qualification_fixture_source_commit,
        'qualification_fixture_git_blob_sha1': bundle.qualification_fixture_git_blob_sha1,
        'qualification_fixture_license': bundle.qualification_fixture_license,
        'validator': bundle.validator,
        'base_evidence_report_sha256': base_evidence_report_sha256,
        'format_qualification_report_sha256': format_qualification_report_sha256,
        'live_archive_size_bytes': live_archive_size_bytes,
        'live_archive_sha256': live_archive_sha256,
        'live_executable_sha256': live_executable_sha256,
        'live_distribution_sha256': live_distribution_sha256,
    })
    return bundle


def bundle_format_evidence_report_files(
    base_evidence_path: Path,
    format_qualification_path: Path,
    *,
    archive_path: Path,
    distribution_root: Path,
    executable_path: Path,
) -> FormatEvidenceBundle:
    base_evidence_path = Path(base_evidence_path)
    format_qualification_path = Path(format_qualification_path)
    try:
        base = json.loads(base_evidence_path.read_text(encoding='utf-8'))
        fq = json.loads(format_qualification_path.read_text(encoding='utf-8'))
    except Exception as e:
        raise FormatEvidenceError(f'failed to read format evidence inputs: {e}') from e
    if not isinstance(base, dict) or not isinstance(fq, dict):
        raise FormatEvidenceError('format evidence inputs must be JSON objects')
    spec = get_distribution_spec(str(base.get('candidate_id') or ''))
    archive_path = Path(archive_path)
    distribution_root = Path(distribution_root).resolve()
    executable_path = Path(executable_path).resolve()
    try:
        executable_path.relative_to(distribution_root)
    except ValueError as e:
        raise FormatEvidenceError('live ExifTool executable is not inside the declared distribution root') from e
    live_size, live_archive_sha = verify_distribution_archive(archive_path, spec)
    if executable_path.is_symlink() or not executable_path.is_file():
        raise FormatEvidenceError('live ExifTool executable is missing or unsafe')
    live_exe_sha = sha256_file(executable_path)
    live_dist_sha = distribution_tree_sha256(distribution_root)
    return build_format_evidence_bundle(
        base, fq,
        base_evidence_report_sha256=sha256_file(base_evidence_path),
        format_qualification_report_sha256=sha256_file(format_qualification_path),
        live_archive_size_bytes=live_size,
        live_archive_sha256=live_archive_sha,
        live_executable_sha256=live_exe_sha,
        live_distribution_sha256=live_dist_sha,
    )


def write_format_evidence_bundle(path: Path, bundle: FormatEvidenceBundle) -> Path:
    path = Path(path)
    write_json_new(path, bundle.to_dict())
    return path
