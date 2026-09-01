from __future__ import annotations

"""Bind composite-relationship qualification to exact format/build evidence.

This is a promotion-review evidence chain, not a production-approval mechanism.
A relationship bundle is only review-ready when the already review-ready format
bundle, the relationship report, and the live ExifTool executable/distribution all
refer to the same exact candidate.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .exiftool_distribution import distribution_tree_sha256
from .format_evidence import FORMAT_EVIDENCE_SCHEMA, expected_format_evidence_bundle_id
from .format_qualification import relationship_profile
from .relationship_roundtrip_qualification import (
    RELATIONSHIP_QUALIFICATION_SCHEMA,
    expected_relationship_qualification_id,
)
from .transactions import sha256_file, write_json_new

RELATIONSHIP_EVIDENCE_SCHEMA = 'gpa.exiftool-relationship-evidence-bundle.v2'


class RelationshipEvidenceError(RuntimeError):
    pass


@dataclass
class RelationshipEvidenceBundle:
    schema: str = RELATIONSHIP_EVIDENCE_SCHEMA
    created_utc: str = ''
    bundle_id: str = ''
    format_evidence_bundle_id: str | None = None
    secondary_format_evidence_bundle_id: str | None = None
    candidate_id: str = ''
    version: str = ''
    executable_sha256: str | None = None
    distribution_sha256: str | None = None
    format_profile_id: str = ''
    secondary_format_profile_id: str | None = None
    relationship_profile_id: str = ''
    relationship_qualification_id: str | None = None
    relationship_qualification_harness: str | None = None
    format_evidence_report_sha256: str | None = None
    secondary_format_evidence_report_sha256: str | None = None
    relationship_qualification_report_sha256: str | None = None
    live_executable_sha256: str | None = None
    live_distribution_sha256: str | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    ready_for_relationship_promotion_review: bool = False
    production_write_approved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _canonical_id(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def expected_relationship_evidence_bundle_id(report: dict) -> str:
    return _canonical_id({
        'schema': str(report.get('schema') or ''),
        'format_evidence_bundle_id': report.get('format_evidence_bundle_id'),
        'secondary_format_evidence_bundle_id': report.get('secondary_format_evidence_bundle_id'),
        'candidate_id': str(report.get('candidate_id') or ''),
        'version': str(report.get('version') or ''),
        'executable_sha256': report.get('executable_sha256'),
        'distribution_sha256': report.get('distribution_sha256'),
        'format_profile_id': str(report.get('format_profile_id') or ''),
        'secondary_format_profile_id': report.get('secondary_format_profile_id'),
        'relationship_profile_id': str(report.get('relationship_profile_id') or ''),
        'relationship_qualification_id': report.get('relationship_qualification_id'),
        'relationship_qualification_harness': report.get('relationship_qualification_harness'),
        'format_evidence_report_sha256': report.get('format_evidence_report_sha256'),
        'secondary_format_evidence_report_sha256': report.get('secondary_format_evidence_report_sha256'),
        'relationship_qualification_report_sha256': report.get('relationship_qualification_report_sha256'),
        'live_executable_sha256': report.get('live_executable_sha256'),
        'live_distribution_sha256': report.get('live_distribution_sha256'),
    })


def build_relationship_evidence_bundle(
    format_evidence: dict,
    relationship_qualification: dict,
    *,
    secondary_format_evidence: dict | None = None,
    format_evidence_report_sha256: str | None = None,
    secondary_format_evidence_report_sha256: str | None = None,
    relationship_qualification_report_sha256: str | None = None,
    live_executable_sha256: str | None = None,
    live_distribution_sha256: str | None = None,
) -> RelationshipEvidenceBundle:
    relationship_id = str(relationship_qualification.get('relationship_profile_id') or '')
    relationship_kind = {
        'motion-photo-composite-v1': 'motion_photo',
        'live-photo-pair-v1': 'live_photo',
    }.get(relationship_id, relationship_id)
    profile = relationship_profile(relationship_kind)
    bundle = RelationshipEvidenceBundle(
        created_utc=datetime.now(timezone.utc).isoformat(),
        format_evidence_bundle_id=format_evidence.get('bundle_id'),
        secondary_format_evidence_bundle_id=(secondary_format_evidence or {}).get('bundle_id'),
        candidate_id=str(format_evidence.get('candidate_id') or ''),
        version=str(format_evidence.get('version') or ''),
        executable_sha256=format_evidence.get('executable_sha256'),
        distribution_sha256=format_evidence.get('distribution_sha256'),
        format_profile_id=str(format_evidence.get('format_profile_id') or ''),
        secondary_format_profile_id=relationship_qualification.get('secondary_format_profile_id'),
        relationship_profile_id=relationship_id,
        relationship_qualification_id=relationship_qualification.get('qualification_id'),
        relationship_qualification_harness=relationship_qualification.get('harness'),
        format_evidence_report_sha256=format_evidence_report_sha256,
        secondary_format_evidence_report_sha256=secondary_format_evidence_report_sha256,
        relationship_qualification_report_sha256=relationship_qualification_report_sha256,
        live_executable_sha256=live_executable_sha256,
        live_distribution_sha256=live_distribution_sha256,
    )
    if profile is None:
        bundle.errors.append(f'unknown relationship profile: {relationship_id or "<empty>"}')
        return bundle
    rq_checks = relationship_qualification.get('checks') or {}
    checks = {
        'format_evidence_schema': format_evidence.get('schema') == FORMAT_EVIDENCE_SCHEMA,
        'format_evidence_review_ready': format_evidence.get('ready_for_format_promotion_review') is True,
        'format_evidence_checks_all_true': isinstance(format_evidence.get('checks'), dict) and bool(format_evidence.get('checks')) and all(v is True for v in format_evidence.get('checks', {}).values()),
        'format_evidence_not_production_approval': format_evidence.get('production_write_approved') is False,
        'format_evidence_bundle_id_valid': isinstance(bundle.format_evidence_bundle_id, str) and bundle.format_evidence_bundle_id == expected_format_evidence_bundle_id(format_evidence),
        'relationship_qualification_schema': relationship_qualification.get('schema') == RELATIONSHIP_QUALIFICATION_SCHEMA,
        'relationship_qualification_passed': relationship_qualification.get('passed') is True and not relationship_qualification.get('errors'),
        'relationship_qualification_checks_all_true': isinstance(rq_checks, dict) and bool(rq_checks) and all(v is True for v in rq_checks.values()),
        'relationship_not_production_approval': relationship_qualification.get('production_write_approved') is False,
        'relationship_profile_registered': profile.profile_id == relationship_id,
        'relationship_harness_registered': bool(profile.qualification_harness) and relationship_qualification.get('harness') == profile.qualification_harness,
        'format_profile_matches': relationship_qualification.get('format_profile_id') == bundle.format_profile_id,
        'candidate_version_matches': relationship_qualification.get('version') == bundle.version,
        'executable_identity_matches': relationship_qualification.get('executable_sha256') == bundle.executable_sha256 and isinstance(bundle.executable_sha256, str) and len(bundle.executable_sha256) == 64,
        'distribution_identity_matches': relationship_qualification.get('distribution_sha256') == bundle.distribution_sha256 and isinstance(bundle.distribution_sha256, str) and len(bundle.distribution_sha256) == 64,
        'relationship_qualification_id_valid': isinstance(bundle.relationship_qualification_id, str) and bundle.relationship_qualification_id == expected_relationship_qualification_id(relationship_qualification),
        'relationship_structure_preserved': rq_checks.get('relationship_structure_preserved') is True,
        'still_payload_unchanged': rq_checks.get('still_payload_unchanged') is True,
        'video_bytes_unchanged': rq_checks.get('video_bytes_unchanged') is True,
        'video_mdat_unchanged': rq_checks.get('video_mdat_unchanged') is True,
        'components_decodable': rq_checks.get('still_decodable_after_write') is True and rq_checks.get('video_decodable_after_write') is True,
        'metadata_readback_exact': rq_checks.get('readback_completed') is True and rq_checks.get('readback_description') is True and rq_checks.get('readback_xmp_date') is True,
        'format_evidence_report_bound': isinstance(format_evidence_report_sha256, str) and len(format_evidence_report_sha256) == 64,
        'relationship_qualification_report_bound': isinstance(relationship_qualification_report_sha256, str) and len(relationship_qualification_report_sha256) == 64,
        'live_executable_identity_matches': isinstance(live_executable_sha256, str) and live_executable_sha256 == bundle.executable_sha256,
        'live_distribution_identity_matches': isinstance(live_distribution_sha256, str) and live_distribution_sha256 == bundle.distribution_sha256,
    }
    secondary_profile_id = relationship_qualification.get('secondary_format_profile_id')
    if secondary_profile_id is not None:
        sf = secondary_format_evidence or {}
        checks.update({
            'secondary_format_evidence_present': isinstance(secondary_format_evidence, dict),
            'secondary_format_evidence_schema': sf.get('schema') == FORMAT_EVIDENCE_SCHEMA,
            'secondary_format_evidence_review_ready': sf.get('ready_for_format_promotion_review') is True,
            'secondary_format_evidence_checks_all_true': isinstance(sf.get('checks'), dict) and bool(sf.get('checks')) and all(v is True for v in sf.get('checks', {}).values()),
            'secondary_format_evidence_not_production_approval': sf.get('production_write_approved') is False,
            'secondary_format_evidence_bundle_id_valid': isinstance(bundle.secondary_format_evidence_bundle_id, str) and bundle.secondary_format_evidence_bundle_id == expected_format_evidence_bundle_id(sf),
            'secondary_format_profile_matches': sf.get('format_profile_id') == secondary_profile_id,
            'secondary_candidate_id_matches': sf.get('candidate_id') == bundle.candidate_id,
            'secondary_candidate_version_matches': sf.get('version') == bundle.version,
            'secondary_executable_identity_matches': sf.get('executable_sha256') == bundle.executable_sha256,
            'secondary_distribution_identity_matches': sf.get('distribution_sha256') == bundle.distribution_sha256,
            'secondary_format_evidence_report_bound': isinstance(secondary_format_evidence_report_sha256, str) and len(secondary_format_evidence_report_sha256) == 64,
            'content_identifier_preserved': rq_checks.get('content_identifier_preserved') is True,
            'timing_resource_preserved': rq_checks.get('timing_resource_preserved') is True,
        })
    elif secondary_format_evidence is not None:
        checks['unexpected_secondary_format_evidence'] = False
    bundle.checks = checks
    bundle.ready_for_relationship_promotion_review = all(checks.values())
    bundle.production_write_approved = False
    bundle.bundle_id = expected_relationship_evidence_bundle_id(bundle.to_dict())
    return bundle


def bundle_relationship_evidence_report_files(
    format_evidence_path: Path,
    relationship_qualification_path: Path,
    *,
    secondary_format_evidence_path: Path | None = None,
    distribution_root: Path,
    executable_path: Path,
) -> RelationshipEvidenceBundle:
    format_evidence_path = Path(format_evidence_path)
    relationship_qualification_path = Path(relationship_qualification_path)
    try:
        fe = json.loads(format_evidence_path.read_text(encoding='utf-8'))
        rq = json.loads(relationship_qualification_path.read_text(encoding='utf-8'))
        sfe = None if secondary_format_evidence_path is None else json.loads(Path(secondary_format_evidence_path).read_text(encoding='utf-8'))
    except Exception as e:
        raise RelationshipEvidenceError(f'failed to read relationship evidence inputs: {e}') from e
    if not isinstance(fe, dict) or not isinstance(rq, dict) or (sfe is not None and not isinstance(sfe, dict)):
        raise RelationshipEvidenceError('relationship evidence inputs must be JSON objects')
    requires_secondary = rq.get('secondary_format_profile_id') is not None
    if requires_secondary and sfe is None:
        raise RelationshipEvidenceError('pair relationship evidence requires the secondary component format-evidence report')
    if not requires_secondary and sfe is not None:
        raise RelationshipEvidenceError('standalone-composite relationship report does not declare a secondary format profile')
    distribution_root = Path(distribution_root).resolve()
    executable_path = Path(executable_path).resolve()
    try:
        executable_path.relative_to(distribution_root)
    except ValueError as e:
        raise RelationshipEvidenceError('live ExifTool executable is not inside the declared distribution root') from e
    if executable_path.is_symlink() or not executable_path.is_file():
        raise RelationshipEvidenceError('live ExifTool executable is missing or unsafe')
    return build_relationship_evidence_bundle(
        fe, rq, secondary_format_evidence=sfe,
        format_evidence_report_sha256=sha256_file(format_evidence_path),
        secondary_format_evidence_report_sha256=(sha256_file(Path(secondary_format_evidence_path)) if secondary_format_evidence_path is not None else None),
        relationship_qualification_report_sha256=sha256_file(relationship_qualification_path),
        live_executable_sha256=sha256_file(executable_path),
        live_distribution_sha256=distribution_tree_sha256(distribution_root),
    )


def write_relationship_evidence_bundle(path: Path, bundle: RelationshipEvidenceBundle) -> Path:
    path = Path(path)
    write_json_new(path, bundle.to_dict())
    return path
