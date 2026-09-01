from __future__ import annotations

"""Fail-closed format-specific embedded-write qualification policy.

ExifTool's ability to write a file type is not archive-safety qualification.  This
module keeps those concepts separate.  A production write needs an exact ExifTool
build/distribution approval *and* an approval for the specific media profile.  Assets
that participate in composite relationships (Live Photo / Motion Photo) require an
additional relationship-level qualification; a plain JPEG or QuickTime approval is
never enough for them.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

FORMAT_MATRIX_SCHEMA = "gpa.format-write-matrix.v8"
FORMAT_APPROVAL_SCHEMA = "gpa.format-write-approval.v3"

STANDALONE = "standalone"
LIVE_PHOTO = "live_photo"
MOTION_PHOTO = "motion_photo"


@dataclass(frozen=True)
class FormatWriteProfile:
    profile_id: str
    suffixes: tuple[str, ...]
    family: str
    payload_fingerprint: str | None
    validator: str | None
    qualification_harness: str | None
    qualification_ready: bool
    blocker: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RelationshipWriteProfile:
    relationship_kind: str
    profile_id: str
    qualification_harness: str | None
    qualification_ready: bool
    blocker: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# "Writable by ExifTool" is intentionally NOT represented here as approval.
# A profile is qualification_ready only when GPA has a payload-preservation method,
# decoder/validator contract and a real round-trip harness for that family.
FORMAT_PROFILES: tuple[FormatWriteProfile, ...] = (
    FormatWriteProfile(
        "jpeg-single-v1", (".jpg", ".jpeg"), "JPEG",
        "jpeg-payload-v1", "Pillow verify + exact media hash",
        "gpa.exiftool-jpeg-roundtrip.v1", True,
    ),
    FormatWriteProfile(
        "png-single-v1", (".png",), "PNG",
        "png-visual-payload-v1", "Pillow verify + exact media hash",
        "gpa.exiftool-png-roundtrip.v1", False,
        "PNG round-trip harness exists, but no exact production candidate has passed and been promoted for this format.",
    ),
    FormatWriteProfile(
        "mov-single-v1", (".mov",), "QuickTime MOV video",
        "iso-bmff-mdat-v1", "ffprobe + exact media hash",
        "gpa.exiftool-quicktime-roundtrip.v1", False,
        "MOV round-trip harness exists, but no exact production candidate has passed and been promoted for this suffix.",
    ),
    FormatWriteProfile(
        "mp4-single-v1", (".mp4",), "ISO-BMFF MP4 video",
        "iso-bmff-mdat-v1", "ffprobe + exact media hash",
        "gpa.exiftool-quicktime-roundtrip.v1", False,
        "MP4 round-trip harness exists, but no exact production candidate has passed and been promoted for this suffix.",
    ),
    FormatWriteProfile(
        "m4v-single-v1", (".m4v",), "ISO-BMFF M4V video",
        "iso-bmff-mdat-v1", "ffprobe + exact media hash",
        "gpa.exiftool-quicktime-roundtrip.v1", False,
        "M4V round-trip harness exists, but no exact production candidate has passed and been promoted for this suffix.",
    ),
    FormatWriteProfile(
        "avif-single-v1", (".avif",), "AVIF still image",
        "heif-item-payload-v1", "libheif/heif-convert + Pillow decode + exact protected-item payload hash",
        "gpa.exiftool-avif-roundtrip.v1", False,
        "AVIF payload fingerprint and round-trip harness exist, but no exact production candidate has passed and been promoted for this format.",
    ),
    FormatWriteProfile(
        "heic-single-v1", (".heic",), "HEIC still image",
        "heif-item-payload-v1", "libheif/heif-convert + Pillow decode + exact protected-item payload hash",
        "gpa.exiftool-heic-roundtrip.v1", False,
        "HEIC pinned-fixture round-trip harness exists, but no exact production candidate has passed and been promoted for this format.",
    ),
    FormatWriteProfile(
        "heif-generic-single-v1", (".heif",), "Generic HEIF still image",
        "heif-item-payload-v1", "libheif/heif-convert + Pillow decode + exact protected-item payload hash",
        None, False,
        "The .heif suffix is codec-generic; content-level codec classification and format-specific round-trip qualification are required before writes can be approved.",
    ),
    FormatWriteProfile(
        "raw-single-v1", (
            ".dng", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".sr2", ".srf",
            ".raf", ".rw2", ".rwl", ".orf", ".pef", ".srw", ".3fr", ".erf", ".mef",
            ".mos", ".mrw", ".x3f", ".iiq", ".raw",
        ), "Camera RAW",
        None, "rawpy/LibRaw isolated read-only decode + exact media hash", None, False,
        "Read-only rawpy/LibRaw decode and representative-corpus qualification now exist, but RAW embedded writes still require format/codec-specific protected-payload fingerprints and writer round-trip qualification; corpus decode success never authorizes rewriting.",
    ),
)

RELATIONSHIP_PROFILES: tuple[RelationshipWriteProfile, ...] = (
    RelationshipWriteProfile(
        LIVE_PHOTO, "live-photo-pair-v1", "gpa.exiftool-live-photo-roundtrip.v1", False,
        "A JPEG+MOV Live Photo relationship round-trip harness now proves content-ID, still-image-time, encoded-media and decode preservation, but no exact Windows candidate has passed/promoted it and genuine Apple Photos acceptance remains a separate gate.",
    ),
    RelationshipWriteProfile(
        MOTION_PHOTO, "motion-photo-composite-v1", "gpa.exiftool-motion-photo-roundtrip.v2", False,
        "JPEG, pinned-HEIC and generated-AVIF Motion Photo 1.0 relationship round-trip lanes now prove primary payload, terminal mpvd/appended video, decode and metadata-readback preservation, but no exact Windows production candidate has passed/promoted the complete format-specific evidence chain.",
    ),
)

_FORMAT_BY_SUFFIX = {
    suffix.casefold(): profile
    for profile in FORMAT_PROFILES
    for suffix in profile.suffixes
}
_REL_BY_KIND = {p.relationship_kind: p for p in RELATIONSHIP_PROFILES}


@dataclass(frozen=True)
class ProductionFormatApproval:
    candidate_version: str
    executable_sha256: str
    distribution_sha256: str
    format_profile_id: str
    relationship_profile_id: str | None = None
    format_evidence_bundle_id: str | None = None
    relationship_evidence_bundle_id: str | None = None
    schema: str = FORMAT_APPROVAL_SCHEMA

    def __post_init__(self) -> None:
        for name, value in (
            ("executable_sha256", self.executable_sha256),
            ("distribution_sha256", self.distribution_sha256),
        ):
            if len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
                raise ValueError(f"{name} must be a SHA-256 hex digest")
        evidence = self.format_evidence_bundle_id or ""
        if len(evidence) != 24 or any(c not in "0123456789abcdefABCDEF" for c in evidence):
            raise ValueError("format_evidence_bundle_id must be a 24-character evidence bundle digest")
        relationship_evidence = self.relationship_evidence_bundle_id
        if self.relationship_profile_id is None:
            if relationship_evidence is not None:
                raise ValueError("standalone approval must not carry relationship_evidence_bundle_id")
        else:
            relationship_evidence = relationship_evidence or ""
            if len(relationship_evidence) != 24 or any(c not in "0123456789abcdefABCDEF" for c in relationship_evidence):
                raise ValueError("composite approval requires a 24-character relationship_evidence_bundle_id")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FormatWriteDecision:
    allowed: bool
    format_profile_id: str | None
    relationship_profile_id: str | None
    reasons: tuple[str, ...]
    schema: str = "gpa.format-write-decision.v1"

    def to_dict(self) -> dict:
        return asdict(self)


def profile_for_suffix(path_or_suffix: str | Path) -> FormatWriteProfile | None:
    raw = str(path_or_suffix)
    suffix = raw if raw.startswith(".") and "/" not in raw and "\\" not in raw else Path(raw).suffix
    return _FORMAT_BY_SUFFIX.get(suffix.casefold())


def relationship_profile(kind: str) -> RelationshipWriteProfile | None:
    return _REL_BY_KIND.get(str(kind))


def format_write_matrix() -> dict:
    return {
        "schema": FORMAT_MATRIX_SCHEMA,
        "formats": [p.to_dict() for p in FORMAT_PROFILES],
        "relationships": [p.to_dict() for p in RELATIONSHIP_PROFILES],
        "production_policy": {
            "requires_exact_build": True,
            "requires_exact_distribution": True,
            "requires_format_approval": True,
            "requires_format_evidence_bundle": True,
            "requires_relationship_evidence_bundle": True,
            "requires_explicit_relationship_kind": True,
            "composite_relationships_require_separate_approval": True,
            "unknown_formats_fail_closed": True,
        },
    }


def authorize_format_write(
    *,
    candidate_version: str,
    executable_sha256: str,
    distribution_sha256: str | None,
    path: str | Path,
    relationship_kind: str,
    approvals: Iterable[ProductionFormatApproval],
) -> FormatWriteDecision:
    """Return a fail-closed production-format decision.

    ``relationship_kind`` is deliberately required rather than defaulted.  A caller
    must explicitly assert that an asset is standalone or identify its composite
    relationship.  This prevents a JPEG approval from silently applying to a Motion
    Photo and a MOV approval from silently applying to a Live Photo component.
    """
    reasons: list[str] = []
    profile = profile_for_suffix(path)
    if profile is None:
        return FormatWriteDecision(False, None, None, ("unrecognized or unqualified media format",))
    if not profile.qualification_ready:
        reasons.append(profile.blocker or "format-specific qualification is not ready")

    rel_profile: RelationshipWriteProfile | None = None
    if relationship_kind == STANDALONE:
        rel_id = None
    else:
        rel_profile = relationship_profile(relationship_kind)
        if rel_profile is None:
            reasons.append("unknown relationship kind; standalone status was not established")
            rel_id = None
        else:
            rel_id = rel_profile.profile_id
            if not rel_profile.qualification_ready:
                reasons.append(rel_profile.blocker or "relationship-specific qualification is not ready")

    if not distribution_sha256:
        reasons.append("exact prepared-distribution SHA-256 is required")

    matching = []
    for approval in approvals:
        if (
            approval.candidate_version == candidate_version
            and approval.executable_sha256.casefold() == executable_sha256.casefold()
            and distribution_sha256 is not None
            and approval.distribution_sha256.casefold() == distribution_sha256.casefold()
            and approval.format_profile_id == profile.profile_id
            and approval.relationship_profile_id == rel_id
        ):
            matching.append(approval)
    if not matching:
        reasons.append("no exact production approval for this build, distribution, format and relationship scope")

    return FormatWriteDecision(not reasons, profile.profile_id, rel_id, tuple(reasons))
