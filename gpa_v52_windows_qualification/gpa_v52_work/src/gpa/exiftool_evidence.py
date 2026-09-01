from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .exiftool_distribution import ADMISSION_SCHEMA, distribution_tree_sha256, get_distribution_spec, verify_distribution_archive
from .exiftool_qualification import HARNESS_ID, QUALIFICATION_SCHEMA
from .transactions import sha256_file, write_json_new

EVIDENCE_SCHEMA = 'gpa.exiftool-evidence-bundle.v1'


class ExifToolEvidenceError(RuntimeError):
    pass


@dataclass
class ExifToolEvidenceBundle:
    schema: str = EVIDENCE_SCHEMA
    created_utc: str = ''
    bundle_id: str = ''
    candidate_id: str = ''
    version: str = ''
    archive_size_bytes: int | None = None
    archive_sha256: str | None = None
    executable_sha256: str | None = None
    distribution_sha256: str | None = None
    qualification_id: str | None = None
    qualification_harness: str | None = None
    admission_report_sha256: str | None = None
    qualification_report_sha256: str | None = None
    live_archive_size_bytes: int | None = None
    live_archive_sha256: str | None = None
    live_executable_sha256: str | None = None
    live_distribution_sha256: str | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    ready_for_promotion_review: bool = False
    production_write_approved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _canonical_id(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def expected_exiftool_evidence_bundle_id(report: dict) -> str:
    """Recompute the canonical identity of a serialized evidence bundle.

    The timestamp, checks, errors and readiness flags are deliberately excluded,
    matching the immutable identity payload used when the bundle is created.
    """
    identity = {
        'schema': EVIDENCE_SCHEMA,
        'candidate_id': report.get('candidate_id'),
        'version': report.get('version'),
        'archive_size_bytes': report.get('archive_size_bytes'),
        'archive_sha256': report.get('archive_sha256'),
        'executable_sha256': report.get('executable_sha256'),
        'distribution_sha256': report.get('distribution_sha256'),
        'qualification_id': report.get('qualification_id'),
        'qualification_harness': report.get('qualification_harness'),
        'admission_report_sha256': report.get('admission_report_sha256'),
        'qualification_report_sha256': report.get('qualification_report_sha256'),
        'live_archive_size_bytes': report.get('live_archive_size_bytes'),
        'live_archive_sha256': report.get('live_archive_sha256'),
        'live_executable_sha256': report.get('live_executable_sha256'),
        'live_distribution_sha256': report.get('live_distribution_sha256'),
    }
    return _canonical_id(identity)


def build_exiftool_evidence_bundle(
    admission: dict,
    qualification: dict,
    *,
    admission_report_sha256: str | None = None,
    qualification_report_sha256: str | None = None,
    live_archive_size_bytes: int | None = None,
    live_archive_sha256: str | None = None,
    live_executable_sha256: str | None = None,
    live_distribution_sha256: str | None = None,
) -> ExifToolEvidenceBundle:
    candidate_id = str(admission.get('candidate_id') or '')
    bundle = ExifToolEvidenceBundle(
        created_utc=datetime.now(timezone.utc).isoformat(),
        candidate_id=candidate_id,
        admission_report_sha256=admission_report_sha256,
        qualification_report_sha256=qualification_report_sha256,
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

    bundle.version = spec.version
    bundle.archive_size_bytes = admission.get('observed_size_bytes')
    bundle.archive_sha256 = admission.get('observed_sha256')
    bundle.executable_sha256 = admission.get('executable_sha256')
    bundle.distribution_sha256 = admission.get('prepared_distribution_sha256')
    bundle.qualification_id = qualification.get('qualification_id')
    bundle.qualification_harness = qualification.get('harness')

    q_exe = qualification.get('executable_sha256')
    q_dist = qualification.get('distribution_sha256')
    checks = {
        'admission_schema': admission.get('schema') == ADMISSION_SCHEMA,
        'qualification_schema': qualification.get('schema') == QUALIFICATION_SCHEMA,
        'candidate_version_matches_pin': admission.get('version') == spec.version,
        'candidate_platform_matches_pin': admission.get('platform') == spec.platform,
        'candidate_architecture_matches_pin': admission.get('architecture') == spec.architecture,
        'archive_verified': admission.get('archive_verified') is True and not admission.get('errors'),
        'archive_size_matches_pin': admission.get('observed_size_bytes') == spec.size_bytes,
        'archive_sha256_matches_pin': str(admission.get('observed_sha256') or '').lower() == spec.sha256.lower(),
        'admission_distribution_prepared': admission.get('checks', {}).get('distribution_prepared') is True,
        'admission_executable_present': admission.get('checks', {}).get('executable_present') is True,
        'prepared_distribution_fingerprinted': isinstance(bundle.distribution_sha256, str) and len(bundle.distribution_sha256) == 64,
        'qualification_passed': qualification.get('passed') is True and not qualification.get('errors'),
        'qualification_harness_matches': qualification.get('harness') == HARNESS_ID,
        'qualification_version_matches_pin': qualification.get('version') == spec.version,
        'executable_identity_matches': isinstance(bundle.executable_sha256, str) and len(bundle.executable_sha256) == 64 and bundle.executable_sha256 == q_exe,
        'distribution_identity_matches': isinstance(bundle.distribution_sha256, str) and len(bundle.distribution_sha256) == 64 and bundle.distribution_sha256 == q_dist,
        'qualification_id_present': isinstance(bundle.qualification_id, str) and bool(bundle.qualification_id),
        'live_archive_size_matches_pin': live_archive_size_bytes == spec.size_bytes,
        'live_archive_sha256_matches_pin': isinstance(live_archive_sha256, str) and live_archive_sha256.lower() == spec.sha256.lower(),
        'live_executable_identity_matches': isinstance(live_executable_sha256, str) and live_executable_sha256 == bundle.executable_sha256 == q_exe,
        'live_distribution_identity_matches': isinstance(live_distribution_sha256, str) and live_distribution_sha256 == bundle.distribution_sha256 == q_dist,
    }
    checks['admission_report_bound'] = isinstance(admission_report_sha256, str) and len(admission_report_sha256) == 64
    checks['qualification_report_bound'] = isinstance(qualification_report_sha256, str) and len(qualification_report_sha256) == 64
    bundle.checks = checks
    bundle.ready_for_promotion_review = all(checks.values())
    # Deliberately never grant production approval here. Promotion is a separate
    # governance decision with format-specific qualification and an exact allow-list.
    bundle.production_write_approved = False
    identity = {
        'schema': EVIDENCE_SCHEMA,
        'candidate_id': candidate_id,
        'version': spec.version,
        'archive_size_bytes': bundle.archive_size_bytes,
        'archive_sha256': bundle.archive_sha256,
        'executable_sha256': bundle.executable_sha256,
        'distribution_sha256': bundle.distribution_sha256,
        'qualification_id': bundle.qualification_id,
        'qualification_harness': bundle.qualification_harness,
        'admission_report_sha256': admission_report_sha256,
        'qualification_report_sha256': qualification_report_sha256,
        'live_archive_size_bytes': live_archive_size_bytes,
        'live_archive_sha256': live_archive_sha256,
        'live_executable_sha256': live_executable_sha256,
        'live_distribution_sha256': live_distribution_sha256,
    }
    bundle.bundle_id = _canonical_id(identity)
    return bundle


def bundle_exiftool_report_files(
    admission_path: Path,
    qualification_path: Path,
    *,
    archive_path: Path,
    distribution_root: Path,
    executable_path: Path,
) -> ExifToolEvidenceBundle:
    admission_path = Path(admission_path)
    qualification_path = Path(qualification_path)
    try:
        admission = json.loads(admission_path.read_text(encoding='utf-8'))
        qualification = json.loads(qualification_path.read_text(encoding='utf-8'))
    except Exception as e:
        raise ExifToolEvidenceError(f'failed to read ExifTool evidence reports: {e}') from e
    if not isinstance(admission, dict) or not isinstance(qualification, dict):
        raise ExifToolEvidenceError('ExifTool evidence reports must contain JSON objects')
    candidate_id = str(admission.get('candidate_id') or '')
    spec = get_distribution_spec(candidate_id)
    archive_path = Path(archive_path)
    distribution_root = Path(distribution_root).resolve()
    executable_path = Path(executable_path).resolve()
    try:
        executable_path.relative_to(distribution_root)
    except ValueError as e:
        raise ExifToolEvidenceError('live ExifTool executable is not inside the declared distribution root') from e
    live_size, live_archive_sha = verify_distribution_archive(archive_path, spec)
    if executable_path.is_symlink() or not executable_path.is_file():
        raise ExifToolEvidenceError('live ExifTool executable is missing or unsafe')
    live_exe_sha = sha256_file(executable_path)
    live_dist_sha = distribution_tree_sha256(distribution_root)
    return build_exiftool_evidence_bundle(
        admission,
        qualification,
        admission_report_sha256=sha256_file(admission_path),
        qualification_report_sha256=sha256_file(qualification_path),
        live_archive_size_bytes=live_size,
        live_archive_sha256=live_archive_sha,
        live_executable_sha256=live_exe_sha,
        live_distribution_sha256=live_dist_sha,
    )


def write_exiftool_evidence_bundle(path: Path, bundle: ExifToolEvidenceBundle) -> Path:
    path = Path(path)
    write_json_new(path, bundle.to_dict())
    return path
