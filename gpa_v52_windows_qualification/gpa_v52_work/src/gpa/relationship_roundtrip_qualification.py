from __future__ import annotations

"""Relationship-level metadata-writer qualification for composite media.

A format-level JPEG pass is not sufficient for a Motion Photo.  This harness builds
one standards-conformant JPEG Motion Photo with a real H.264/MP4 tail and proves that
an ExifTool metadata mutation preserves both the still-image payload and the exact
appended video while retaining a structurally valid Motion Photo directory.

Passing evidence is qualification-only.  It never grants production write approval.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from .exiftool import ExifToolAdapter, executable_command
from .exiftool_qualification import fingerprint_exiftool_candidate
from .fingerprint import (jpeg_payload_fingerprint, mp4_mdat_fingerprint, heif_item_payload_fingerprint,
                          _box_spans, _fullbox, _parse_iinf, _parse_iloc)
from .media_validation import validate_media
from .live_photo_integrity import inspect_live_photo_pair
from .motion import detect_jpeg_motion_photo, detect_heif_motion_photo
from .qualification_fixtures import HEIC_FIXTURE, verify_qualification_fixture
from .transactions import sha256_file, write_json_new
from .writepolicy import MetadataPlan

RELATIONSHIP_QUALIFICATION_SCHEMA = 'gpa.exiftool-relationship-qualification.v1'
MOTION_PHOTO_HARNESS_ID = 'gpa.exiftool-motion-photo-roundtrip.v2'
MOTION_PHOTO_PROFILE_ID = 'motion-photo-composite-v1'
LIVE_PHOTO_HARNESS_ID = 'gpa.exiftool-live-photo-roundtrip.v1'
LIVE_PHOTO_PROFILE_ID = 'live-photo-pair-v1'
JPEG_PROFILE_ID = 'jpeg-single-v1'
MOV_PROFILE_ID = 'mov-single-v1'
HEIC_PROFILE_ID = 'heic-single-v1'
AVIF_PROFILE_ID = 'avif-single-v1'


class RelationshipQualificationError(RuntimeError):
    pass


@dataclass
class RelationshipQualificationReport:
    schema: str = RELATIONSHIP_QUALIFICATION_SCHEMA
    harness: str = MOTION_PHOTO_HARNESS_ID
    relationship_profile_id: str = MOTION_PHOTO_PROFILE_ID
    format_profile_id: str = JPEG_PROFILE_ID
    secondary_format_profile_id: str | None = None
    created_utc: str = ''
    qualification_id: str = ''
    passed: bool = False
    executable: str = ''
    version: str | None = None
    executable_sha256: str | None = None
    distribution_root: str | None = None
    distribution_sha256: str | None = None
    fixture_path: str | None = None
    fixture_generator: dict = field(default_factory=dict)
    validators: dict = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    observations: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    production_write_approved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _qualification_identity_payload(*, harness: str, relationship_profile_id: str, format_profile_id: str,
                                    secondary_format_profile_id: str | None, version: str,
                                    executable_sha256: str, distribution_sha256: str | None) -> dict:
    payload = {
        'schema': RELATIONSHIP_QUALIFICATION_SCHEMA,
        'harness': harness,
        'relationship_profile_id': relationship_profile_id,
        'format_profile_id': format_profile_id,
        'version': version,
        'executable_sha256': executable_sha256,
        'distribution_sha256': distribution_sha256,
    }
    # Preserve the canonical v1 Motion Photo ID when no second component format is
    # involved; pair relationships explicitly bind both component profiles.
    if secondary_format_profile_id is not None:
        payload['secondary_format_profile_id'] = secondary_format_profile_id
    return payload


def _qualification_id(*, harness: str = MOTION_PHOTO_HARNESS_ID,
                      relationship_profile_id: str = MOTION_PHOTO_PROFILE_ID,
                      format_profile_id: str = JPEG_PROFILE_ID,
                      secondary_format_profile_id: str | None = None,
                      version: str, executable_sha256: str, distribution_sha256: str | None) -> str:
    payload = _qualification_identity_payload(
        harness=harness, relationship_profile_id=relationship_profile_id,
        format_profile_id=format_profile_id, secondary_format_profile_id=secondary_format_profile_id,
        version=version, executable_sha256=executable_sha256, distribution_sha256=distribution_sha256,
    )
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def expected_relationship_qualification_id(report: dict) -> str:
    payload = _qualification_identity_payload(
        harness=str(report.get('harness') or ''),
        relationship_profile_id=str(report.get('relationship_profile_id') or ''),
        format_profile_id=str(report.get('format_profile_id') or ''),
        secondary_format_profile_id=report.get('secondary_format_profile_id'),
        version=str(report.get('version') or ''),
        executable_sha256=str(report.get('executable_sha256') or ''),
        distribution_sha256=report.get('distribution_sha256'),
    )
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def _candidate_version(executable: Path, timeout_seconds: float) -> str:
    try:
        cp = subprocess.run(executable_command(executable, '-ver'), capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as e:
        raise RelationshipQualificationError(f'ExifTool version check timed out after {timeout_seconds:g}s') from e
    if cp.returncode:
        raise RelationshipQualificationError(cp.stderr.strip() or 'ExifTool version check failed')
    version = cp.stdout.strip()
    if not version:
        raise RelationshipQualificationError('ExifTool returned an empty version')
    return version


def _tool_identity(executable: str) -> dict:
    resolved = shutil.which(executable)
    if not resolved:
        raise RelationshipQualificationError(f'{executable} is not installed')
    p = Path(resolved).resolve()
    try:
        cp = subprocess.run(executable_command(p, '-version'), capture_output=True, text=True, timeout=10, check=False)
        first = (cp.stdout or cp.stderr or '').strip().splitlines()
        version_line = first[0] if first else None
    except Exception:
        version_line = None
    return {'tool': executable, 'path': str(p), 'sha256': sha256_file(p), 'version_line': version_line}


def _jpeg_bytes() -> bytes:
    from PIL import Image
    image = Image.new('RGB', (41, 31))
    pix = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pix[x, y] = ((x * 23 + y * 5) % 256, (x * 7 + y * 19) % 256, (x * 13 + y * 11) % 256)
    out = BytesIO()
    image.save(out, 'JPEG', quality=91, optimize=False)
    return out.getvalue()


def _inject_standard_xmp(jpeg: bytes, xmp: bytes) -> bytes:
    header = b'http://ns.adobe.com/xap/1.0/\x00'
    payload = header + xmp
    if len(payload) + 2 > 0xFFFF:
        raise RelationshipQualificationError('Motion Photo XMP fixture exceeds one JPEG APP1 segment')
    if not jpeg.startswith(b'\xff\xd8'):
        raise RelationshipQualificationError('fixture builder did not produce JPEG')
    segment = b'\xff\xe1' + (len(payload) + 2).to_bytes(2, 'big') + payload
    return jpeg[:2] + segment + jpeg[2:]


def _motion_xmp(video_length: int, *, primary_mime: str = 'image/jpeg', primary_padding: int = 0) -> bytes:
    # Motion Photo 1.0: the primary item is first; secondary video has exact Length.
    # HEIC/AVIF require primary Padding=8 for the terminal mpvd box header, while
    # JPEG permits zero/omitted padding. Legacy MicroVideo fields are absent.
    xml = f'''<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:Camera="http://ns.google.com/photos/1.0/camera/" xmlns:Container="http://ns.google.com/photos/1.0/container/" xmlns:Item="http://ns.google.com/photos/1.0/container/item/">
<rdf:RDF><rdf:Description Camera:MotionPhoto="1" Camera:MotionPhotoVersion="1" Camera:MotionPhotoPresentationTimestampUs="-1">
<Container:Directory><rdf:Seq>
<rdf:li rdf:parseType="Resource" Item:Mime="{primary_mime}" Item:Semantic="Primary" Item:Length="0" Item:Padding="{primary_padding}"/>
<rdf:li rdf:parseType="Resource" Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="{video_length}"/>
</rdf:Seq></Container:Directory></rdf:Description></rdf:RDF></x:xmpmeta>'''
    return xml.encode('utf-8')


def _generate_motion_video(path: Path, timeout_seconds: float) -> tuple[bytes, dict]:
    ffmpeg = _tool_identity('ffmpeg')
    video_path = path.with_suffix('.fixture-video.mp4')
    cmd = [
        ffmpeg['path'], '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc=size=64x48:rate=6', '-t', '1',
        '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video_path),
    ]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as e:
        raise RelationshipQualificationError(f'ffmpeg Motion Photo fixture generation timed out after {timeout_seconds:g}s') from e
    if cp.returncode or not video_path.is_file():
        raise RelationshipQualificationError(cp.stderr.strip() or 'ffmpeg failed to generate Motion Photo video fixture')
    video = video_path.read_bytes()
    video_path.unlink(missing_ok=True)
    try:
        video_mdat_sha256 = mp4_mdat_fingerprint(video)
    except Exception as e:
        raise RelationshipQualificationError(f'generated Motion Photo video is structurally invalid: {e}') from e
    return video, {
        'video': ffmpeg,
        'video_codec': 'H.264/AVC via libx264',
        'video_length': len(video),
        'video_sha256': hashlib.sha256(video).hexdigest(),
        'video_mdat_sha256': video_mdat_sha256,
    }


def _build_motion_photo_fixture(path: Path, timeout_seconds: float) -> dict:
    video, generator = _generate_motion_video(path, timeout_seconds)
    still = _inject_standard_xmp(_jpeg_bytes(), _motion_xmp(len(video)))
    path.write_bytes(still + video)
    return {
        'primary_image': 'Pillow JPEG',
        **generator,
        'motion_photo_spec': 'Android Motion Photo format 1.0',
        'primary_padding': 0,
        'video_container': 'raw appended ISO-BMFF video bytes',
        'legacy_microvideo_fields_present': False,
    }


def _heif_xmp_item_ranges(data: bytes) -> tuple[int, tuple[tuple[int, int], ...]]:
    top = list(_box_spans(data))
    metas = [row for row in top if row[0] == b'meta']
    if len(metas) != 1:
        raise RelationshipQualificationError('HEIC/AVIF fixture must contain exactly one file-level meta box')
    _typ, _start, meta_payload, meta_end = metas[0]
    _mv, _mf, child_start = _fullbox(data, meta_payload, meta_end)
    children = list(_box_spans(data, child_start, meta_end))
    iinf = [row for row in children if row[0] == b'iinf']
    iloc = [row for row in children if row[0] == b'iloc']
    idat = [row for row in children if row[0] == b'idat']
    if len(iinf) != 1 or len(iloc) != 1 or len(idat) > 1:
        raise RelationshipQualificationError('HEIC/AVIF fixture has unsupported XMP item metadata layout')
    items = _parse_iinf(data, iinf[0][2], iinf[0][3])
    mdat_payloads = [(payload, end) for typ, _s, payload, end in top if typ == b'mdat']
    idat_payload = (idat[0][2], idat[0][3]) if idat else None
    locations = _parse_iloc(data, iloc[0][2], iloc[0][3], mdat_payloads=mdat_payloads, idat_payload=idat_payload)
    matches = [item_id for item_id, (item_type, _name, content_type) in items.items()
               if item_type == b'mime' and content_type == b'application/rdf+xml']
    if len(matches) != 1:
        raise RelationshipQualificationError('HEIC/AVIF fixture must contain exactly one XMP metadata item')
    ranges = locations.get(matches[0])
    if not ranges:
        raise RelationshipQualificationError('HEIC/AVIF XMP item has no file-local payload')
    return matches[0], tuple(ranges)


def _replace_heif_xmp_fixed_size(data: bytes, xmp: bytes) -> bytes:
    _item_id, ranges = _heif_xmp_item_ranges(data)
    capacity = sum(b - a for a, b in ranges)
    if len(xmp) > capacity:
        raise RelationshipQualificationError(
            f'Motion Photo XMP requires {len(xmp)} bytes but pinned HEIC metadata item has only {capacity}'
        )
    # Trailing XML whitespace is valid and keeps all HEIF item extents byte-stable.
    payload = xmp + b' ' * (capacity - len(xmp))
    out = bytearray(data)
    pos = 0
    for a, b in ranges:
        n = b - a
        out[a:b] = payload[pos:pos+n]
        pos += n
    if pos != len(payload):
        raise RelationshipQualificationError('failed to fill HEIC/AVIF XMP extents exactly')
    return bytes(out)


def _avif_motion_primary(xmp: bytes) -> tuple[bytes, dict]:
    import PIL
    from PIL import Image
    image = Image.new('RGB', (43, 29))
    pix = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pix[x, y] = ((x * 17 + y * 3) % 256, (x * 5 + y * 29) % 256, (x * 31 + y * 7) % 256)
    out = BytesIO()
    try:
        image.save(out, 'AVIF', quality=72, xmp=xmp)
    except Exception as e:
        raise RelationshipQualificationError(f'Pillow could not generate AVIF Motion Photo primary fixture: {e}') from e
    data = out.getvalue()
    # Force parsing now so a Pillow build that silently drops XMP cannot qualify.
    _heif_xmp_item_ranges(data)
    return data, {'tool': 'Pillow AVIF encoder', 'version': str(PIL.__version__)}


def _build_heif_motion_photo_fixture(
    path: Path,
    timeout_seconds: float,
    *,
    format_profile_id: str,
    qualification_fixture: Path | None,
) -> dict:
    if format_profile_id not in {HEIC_PROFILE_ID, AVIF_PROFILE_ID}:
        raise RelationshipQualificationError(f'unsupported ISOBMFF Motion Photo profile: {format_profile_id}')
    video, generator = _generate_motion_video(path, timeout_seconds)
    primary_mime = 'image/heic' if format_profile_id == HEIC_PROFILE_ID else 'image/avif'
    xmp = _motion_xmp(len(video), primary_mime=primary_mime, primary_padding=8)
    fixture_admission = None
    if format_profile_id == HEIC_PROFILE_ID:
        if qualification_fixture is None:
            raise RelationshipQualificationError(
                'heic-single-v1 Motion Photo qualification requires --fixture with the exact pinned HEIC qualification fixture'
            )
        fixture_admission = verify_qualification_fixture(Path(qualification_fixture), HEIC_FIXTURE)
        pinned_source = Path(qualification_fixture).read_bytes()
        pinned_payload = heif_item_payload_fingerprint(pinned_source)
        primary = _replace_heif_xmp_fixed_size(pinned_source, xmp)
        patched_payload = heif_item_payload_fingerprint(primary)
        if patched_payload != pinned_payload:
            raise RelationshipQualificationError('HEIC Motion Photo XMP fixture construction changed protected image payload')
        primary_generator = {
            'tool': 'pinned libheif HEIC fixture + fixed-size XMP metadata replacement',
            'fixture_admission': fixture_admission,
            'protected_payload_preserved_from_pinned_fixture': True,
            'protected_payload_sha256': pinned_payload,
        }
    else:
        primary, primary_generator = _avif_motion_primary(xmp)
    # Motion Photo 1.0 requires a terminal, explicit-size mpvd box. The Item:Length
    # is the video payload only and Primary Padding=8 accounts for this box header.
    if len(video) + 8 >= 2**32:
        raise RelationshipQualificationError('Motion Photo video fixture is too large for a 32-bit mpvd box')
    mpvd = (len(video) + 8).to_bytes(4, 'big') + b'mpvd' + video
    path.write_bytes(primary + mpvd)
    return {
        'primary_image': primary_generator,
        **generator,
        'motion_photo_spec': 'Android Motion Photo format 1.0',
        'primary_mime': primary_mime,
        'primary_padding': 8,
        'video_container': 'terminal explicit-size mpvd box',
        'fixture_admission': fixture_admission,
        'legacy_microvideo_fields_present': False,
    }


def _find_suffix(tags: dict, suffix: str):
    if suffix in tags:
        return tags[suffix]
    for key, value in tags.items():
        if str(key).split(':')[-1] == suffix:
            return value
    return None


def _relationship_state(data: bytes, format_profile_id: str) -> dict:
    if format_profile_id == JPEG_PROFILE_ID:
        detected = detect_jpeg_motion_photo(data, allow_legacy_microvideo=False)
        if not detected:
            raise RelationshipQualificationError('JPEG no longer satisfies Motion Photo 1.0 structural detection')
        descriptor, video = detected
        expected_mime = 'image/jpeg'
        primary = descriptor.primary_item
        if not primary or primary.mime != expected_mime or primary.length not in (None, 0) or primary.padding not in (None, 0):
            raise RelationshipQualificationError('Motion Photo JPEG primary item contract changed')
        still = data[:video.start]
        still_payload_sha256 = jpeg_payload_fingerprint(still)
        mpvd_header_exact = None
    elif format_profile_id in {HEIC_PROFILE_ID, AVIF_PROFILE_ID}:
        detected = detect_heif_motion_photo(data)
        if not detected:
            raise RelationshipQualificationError('HEIC/AVIF no longer satisfies Motion Photo 1.0 structural detection')
        descriptor, video = detected
        expected_mime = 'image/heic' if format_profile_id == HEIC_PROFILE_ID else 'image/avif'
        primary = descriptor.primary_item
        if not primary or primary.mime != expected_mime or primary.length not in (None, 0) or primary.padding != 8:
            raise RelationshipQualificationError('Motion Photo HEIC/AVIF primary item contract changed')
        header_start = video.start - 8
        if header_start < 0 or data[header_start + 4:video.start] != b'mpvd':
            raise RelationshipQualificationError('terminal Motion Photo video is missing the exact 8-byte mpvd header')
        declared = int.from_bytes(data[header_start:header_start + 4], 'big')
        if declared == 0 or declared != (video.end - video.start) + 8:
            raise RelationshipQualificationError('mpvd box must use an explicit size equal to header plus video bytes')
        still = data[:header_start]
        still_payload_sha256 = heif_item_payload_fingerprint(still)
        mpvd_header_exact = True
    else:
        raise RelationshipQualificationError(f'unsupported Motion Photo format profile: {format_profile_id}')

    if descriptor.warnings:
        raise RelationshipQualificationError('Motion Photo descriptor is ambiguous or malformed: ' + '; '.join(descriptor.warnings))
    motion = descriptor.motion_item
    if [x.semantic for x in descriptor.items] != ['Primary', 'MotionPhoto']:
        raise RelationshipQualificationError('Motion Photo directory item order or cardinality changed')
    if not motion or motion.mime != 'video/mp4' or not motion.length or motion.length <= 0:
        raise RelationshipQualificationError('Motion Photo video item contract changed')
    if descriptor.version != 1 or descriptor.motion_flag != 1:
        raise RelationshipQualificationError('Motion Photo version/flag contract changed')
    if descriptor.legacy_microvideo_offset is not None:
        raise RelationshipQualificationError('fixture unexpectedly depends on legacy MicroVideoOffset')
    if video.end != len(data) or video.end - video.start != motion.length:
        raise RelationshipQualificationError('Motion Photo video is not exact terminal Length resource')
    tail = data[video.start:video.end]
    return {
        'descriptor': descriptor,
        'video': video,
        'still_bytes': still,
        'video_bytes': tail,
        'still_payload_sha256': still_payload_sha256,
        'video_sha256': hashlib.sha256(tail).hexdigest(),
        'video_mdat_sha256': mp4_mdat_fingerprint(tail),
        'video_length': len(tail),
        'mpvd_header_exact': mpvd_header_exact,
    }


def _validate_component_bytes(workdir: Path, still: bytes, video: bytes, *, still_suffix: str) -> tuple[dict, dict]:
    still_path = workdir / f'component-primary{still_suffix}'
    video_path = workdir / 'component-motion.mp4'
    still_path.write_bytes(still)
    video_path.write_bytes(video)
    s = validate_media(still_path)
    v = validate_media(video_path)
    still_path.unlink(missing_ok=True)
    video_path.unlink(missing_ok=True)
    return s.to_dict(), v.to_dict()


def qualify_exiftool_motion_photo(
    executable: Path,
    workdir: Path,
    *,
    distribution_root: Path | None = None,
    format_profile_id: str = JPEG_PROFILE_ID,
    qualification_fixture: Path | None = None,
    subprocess_timeout_seconds: float = 30.0,
) -> RelationshipQualificationReport:
    if subprocess_timeout_seconds <= 0:
        raise ValueError('subprocess_timeout_seconds must be > 0')
    if format_profile_id not in {JPEG_PROFILE_ID, HEIC_PROFILE_ID, AVIF_PROFILE_ID}:
        raise ValueError(f'unsupported Motion Photo format profile: {format_profile_id}')
    if format_profile_id != HEIC_PROFILE_ID and qualification_fixture is not None:
        raise ValueError('--fixture is only valid for heic-single-v1 Motion Photo qualification')
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    report = RelationshipQualificationReport(
        created_utc=datetime.now(timezone.utc).isoformat(),
        harness=MOTION_PHOTO_HARNESS_ID,
        relationship_profile_id=MOTION_PHOTO_PROFILE_ID,
        format_profile_id=format_profile_id,
    )
    try:
        fp = fingerprint_exiftool_candidate(Path(executable), distribution_root)
        report.executable = fp['executable']
        report.executable_sha256 = fp['executable_sha256']
        report.distribution_root = fp['distribution_root']
        report.distribution_sha256 = fp['distribution_sha256']
        version = _candidate_version(Path(report.executable), subprocess_timeout_seconds)
        report.version = version
        report.qualification_id = _qualification_id(
            harness=MOTION_PHOTO_HARNESS_ID,
            relationship_profile_id=MOTION_PHOTO_PROFILE_ID,
            format_profile_id=format_profile_id,
            version=version,
            executable_sha256=report.executable_sha256,
            distribution_sha256=report.distribution_sha256,
        )
        report.checks['version_nonempty'] = bool(version)
        report.checks['executable_fingerprinted'] = len(report.executable_sha256 or '') == 64
        if distribution_root is not None:
            report.checks['distribution_fingerprinted'] = len(report.distribution_sha256 or '') == 64

        suffix = {JPEG_PROFILE_ID: '.jpg', HEIC_PROFILE_ID: '.heic', AVIF_PROFILE_ID: '.avif'}[format_profile_id]
        fixture = workdir / f'qualification.MP{suffix}'
        if format_profile_id == JPEG_PROFILE_ID:
            report.fixture_generator = _build_motion_photo_fixture(fixture, subprocess_timeout_seconds)
        else:
            report.fixture_generator = _build_heif_motion_photo_fixture(
                fixture, subprocess_timeout_seconds, format_profile_id=format_profile_id,
                qualification_fixture=qualification_fixture,
            )
        report.fixture_path = str(fixture)
        report.checks['fixture_created'] = fixture.is_file()
        if format_profile_id == HEIC_PROFILE_ID:
            admission = (report.fixture_generator.get('fixture_admission') or {})
            report.checks['fixture_source_admitted'] = admission.get('exact_bytes_verified') is True and admission.get('source_git_blob_verified') is True
            primary_gen = report.fixture_generator.get('primary_image') or {}
            report.checks['fixture_pinned_primary_payload_preserved'] = primary_gen.get('protected_payload_preserved_from_pinned_fixture') is True

        before = fixture.read_bytes()
        before_sha = hashlib.sha256(before).hexdigest()
        before_state = _relationship_state(before, format_profile_id)
        report.checks['fixture_motion_structure_valid'] = True
        report.checks['fixture_uses_motion_photo_v1_not_legacy'] = before_state['descriptor'].legacy_microvideo_offset is None
        if format_profile_id in {HEIC_PROFILE_ID, AVIF_PROFILE_ID}:
            report.checks['fixture_mpvd_header_exact'] = before_state['mpvd_header_exact'] is True
        before_still_validation, before_video_validation = _validate_component_bytes(
            workdir, before_state['still_bytes'], before_state['video_bytes'], still_suffix=suffix
        )
        report.observations['before_still_validation'] = before_still_validation
        report.observations['before_video_validation'] = before_video_validation
        report.checks['fixture_still_decodable'] = before_still_validation.get('status') == 'passed' and before_still_validation.get('bytes_unchanged') is True
        report.checks['fixture_video_decodable'] = before_video_validation.get('status') == 'passed' and before_video_validation.get('bytes_unchanged') is True

        plan = MetadataPlan(xmp={
            'XMP-photoshop:DateCreated': '2017-05-01T21:30:00+09:00',
            'dc:description': 'GPA Motion Photo relationship qualification fixture',
        })
        adapter = ExifToolAdapter(report.executable, approved_versions=(version,), timeout_seconds=subprocess_timeout_seconds)
        try:
            report.observations['write_output'] = adapter.write(fixture, plan)
            report.checks['write_completed'] = True
        except Exception as e:
            report.checks['write_completed'] = False
            report.errors.append(f'write failed: {e}')

        if fixture.is_file():
            after = fixture.read_bytes()
            report.observations['before_sha256'] = before_sha
            report.observations['after_sha256'] = hashlib.sha256(after).hexdigest()
            report.checks['whole_file_changed'] = after != before
            try:
                after_state = _relationship_state(after, format_profile_id)
                report.checks['relationship_structure_preserved'] = True
                report.checks['still_payload_unchanged'] = after_state['still_payload_sha256'] == before_state['still_payload_sha256']
                report.checks['video_bytes_unchanged'] = after_state['video_sha256'] == before_state['video_sha256']
                report.checks['video_mdat_unchanged'] = after_state['video_mdat_sha256'] == before_state['video_mdat_sha256']
                report.checks['video_length_preserved'] = after_state['video_length'] == before_state['video_length']
                report.checks['video_terminal_after_write'] = after_state['video'].end == len(after)
                report.checks['motion_v1_not_legacy_after_write'] = after_state['descriptor'].legacy_microvideo_offset is None
                if format_profile_id in {HEIC_PROFILE_ID, AVIF_PROFILE_ID}:
                    report.checks['mpvd_header_preserved'] = after_state['mpvd_header_exact'] is True
                still_validation, video_validation = _validate_component_bytes(
                    workdir, after_state['still_bytes'], after_state['video_bytes'], still_suffix=suffix
                )
                report.validators = {'still': still_validation, 'video': video_validation}
                report.checks['still_decodable_after_write'] = still_validation.get('status') == 'passed' and still_validation.get('bytes_unchanged') is True
                report.checks['video_decodable_after_write'] = video_validation.get('status') == 'passed' and video_validation.get('bytes_unchanged') is True
                report.checks['still_validator_fingerprinted'] = isinstance(still_validation.get('validator_sha256'), str) and len(still_validation.get('validator_sha256')) == 64
                report.checks['video_validator_fingerprinted'] = isinstance(video_validation.get('validator_sha256'), str) and len(video_validation.get('validator_sha256')) == 64
            except Exception as e:
                for key in (
                    'relationship_structure_preserved', 'still_payload_unchanged', 'video_bytes_unchanged',
                    'video_mdat_unchanged', 'video_length_preserved', 'video_terminal_after_write',
                    'motion_v1_not_legacy_after_write', 'still_decodable_after_write',
                    'video_decodable_after_write', 'still_validator_fingerprinted', 'video_validator_fingerprinted',
                ):
                    report.checks[key] = False
                if format_profile_id in {HEIC_PROFILE_ID, AVIF_PROFILE_ID}:
                    report.checks['mpvd_header_preserved'] = False
                report.errors.append(f'relationship verification failed: {e}')
        else:
            report.checks['whole_file_changed'] = False
            report.errors.append('candidate removed qualification fixture')

        try:
            tags = adapter.read(fixture)
            report.checks['readback_completed'] = True
            expected_desc = 'GPA Motion Photo relationship qualification fixture'
            desc = _find_suffix(tags, 'Description')
            report.checks['readback_description'] = desc == expected_desc or (isinstance(desc, list) and expected_desc in desc)
            date_created = _find_suffix(tags, 'DateCreated')
            report.checks['readback_xmp_date'] = str(date_created or '').replace(':', '-', 2).replace(' ', 'T', 1).startswith('2017-05-01T21:30:00')
        except Exception as e:
            report.checks['readback_completed'] = False
            report.checks['readback_description'] = False
            report.checks['readback_xmp_date'] = False
            report.errors.append(f'readback failed: {e}')
    except Exception as e:
        report.errors.append(str(e))

    required = {
        'version_nonempty', 'executable_fingerprinted', 'fixture_created',
        'fixture_motion_structure_valid', 'fixture_uses_motion_photo_v1_not_legacy',
        'fixture_still_decodable', 'fixture_video_decodable', 'write_completed', 'whole_file_changed',
        'relationship_structure_preserved', 'still_payload_unchanged', 'video_bytes_unchanged',
        'video_mdat_unchanged', 'video_length_preserved', 'video_terminal_after_write',
        'motion_v1_not_legacy_after_write', 'still_decodable_after_write', 'video_decodable_after_write',
        'still_validator_fingerprinted', 'video_validator_fingerprinted',
        'readback_completed', 'readback_description', 'readback_xmp_date',
    }
    if distribution_root is not None:
        required.add('distribution_fingerprinted')
    if format_profile_id == HEIC_PROFILE_ID:
        required.update({'fixture_source_admitted', 'fixture_pinned_primary_payload_preserved'})
    if format_profile_id in {HEIC_PROFILE_ID, AVIF_PROFILE_ID}:
        required.update({'fixture_mpvd_header_exact', 'mpvd_header_preserved'})
    report.passed = all(report.checks.get(key) is True for key in required) and not report.errors
    return report


def _apple_makernote_for_fixture(content_identifier: str) -> bytes:
    value = content_identifier.encode('ascii') + b'\x00'
    header = b'Apple iOS\x00\x00\x01MM'
    value_offset = 14 + 2 + 12 + 4
    entry = (0x0011).to_bytes(2, 'big') + (2).to_bytes(2, 'big') + len(value).to_bytes(4, 'big') + value_offset.to_bytes(4, 'big')
    return header + (1).to_bytes(2, 'big') + entry + (0).to_bytes(4, 'big') + value


def _live_photo_jpeg_bytes(content_identifier: str) -> bytes:
    jpeg = _jpeg_bytes()
    maker = _apple_makernote_for_fixture(content_identifier)
    # Big-endian TIFF: IFD0 -> ExifIFD -> Apple MakerNote.  This is deliberately
    # minimal qualification metadata; image bytes remain a genuine Pillow JPEG.
    tiff_header = b'MM\x00*' + (8).to_bytes(4, 'big')
    ifd0 = (1).to_bytes(2, 'big') + (0x8769).to_bytes(2, 'big') + (4).to_bytes(2, 'big') + (1).to_bytes(4, 'big') + (26).to_bytes(4, 'big') + (0).to_bytes(4, 'big')
    maker_off = 44
    exif_ifd = (1).to_bytes(2, 'big') + (0x927C).to_bytes(2, 'big') + (7).to_bytes(2, 'big') + len(maker).to_bytes(4, 'big') + maker_off.to_bytes(4, 'big') + (0).to_bytes(4, 'big')
    payload = b'Exif\x00\x00' + tiff_header + ifd0 + exif_ifd + maker
    if len(payload) + 2 > 0xFFFF:
        raise RelationshipQualificationError('Live Photo EXIF fixture is too large')
    app1 = b'\xff\xe1' + (len(payload) + 2).to_bytes(2, 'big') + payload
    return jpeg[:2] + app1 + jpeg[2:]


def _qt_box(typ: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, 'big') + typ + payload


def _qt_fullbox_fixture(typ: bytes, payload: bytes, *, version: int = 0, flags: int = 0) -> bytes:
    return _qt_box(typ, bytes([version]) + flags.to_bytes(3, 'big') + payload)


def _live_photo_timing_track(*, still_time_movie_ticks: int, movie_timescale: int) -> bytes:
    if still_time_movie_ticks < 0 or movie_timescale <= 0:
        raise RelationshipQualificationError('invalid Live Photo timing fixture parameters')
    key = b'com.apple.quicktime.still-image-time'
    keyd = _qt_box(b'keyd', b'mdta' + key)
    keys = _qt_box(b'keys', _qt_box((1).to_bytes(4, 'big'), keyd))
    mebx = _qt_box(b'mebx', b'\x00' * 8 + keys)
    stsd = _qt_fullbox_fixture(b'stsd', (1).to_bytes(4, 'big') + mebx)
    stts = _qt_fullbox_fixture(b'stts', (1).to_bytes(4, 'big') + (1).to_bytes(4, 'big') + (1).to_bytes(4, 'big'))
    stbl = _qt_box(b'stbl', stsd + stts)
    minf = _qt_box(b'minf', stbl)
    mdhd = _qt_fullbox_fixture(b'mdhd', b'\x00' * 8 + (600).to_bytes(4, 'big') + (1).to_bytes(4, 'big'))
    hdlr = _qt_fullbox_fixture(b'hdlr', b'\x00' * 4 + b'meta')
    mdia = _qt_box(b'mdia', mdhd + hdlr + minf)
    rows = []
    if still_time_movie_ticks:
        rows.append(still_time_movie_ticks.to_bytes(4, 'big') + (-1).to_bytes(4, 'big', signed=True) + b'\x00\x01\x00\x00')
    rows.append((1).to_bytes(4, 'big') + (0).to_bytes(4, 'big', signed=True) + b'\x00\x01\x00\x00')
    elst = _qt_fullbox_fixture(b'elst', len(rows).to_bytes(4, 'big') + b''.join(rows))
    return _qt_box(b'trak', _qt_box(b'edts', elst) + mdia)


def _inject_live_photo_timing_track(movie: bytes, *, still_time_seconds: float) -> bytes:
    # Reuse the independent parser's strict top-level box model.  The fixture is
    # generated without faststart so mdat precedes the terminal moov; enlarging the
    # terminal moov cannot change any video chunk offsets or mdat bytes.
    from .live_photo_integrity import _qt_box_spans, _qt_mvhd_timescale
    spans = list(_qt_box_spans(movie))
    moovs = [row for row in spans if row[0] == b'moov']
    if len(moovs) != 1 or moovs[0][3] != len(movie):
        raise RelationshipQualificationError('Live Photo fixture requires one terminal moov box')
    _typ, moov_start, moov_payload, moov_end = moovs[0]
    mvhd = [row for row in _qt_box_spans(movie, moov_payload, moov_end) if row[0] == b'mvhd']
    if len(mvhd) != 1:
        raise RelationshipQualificationError('Live Photo fixture MOV has no unambiguous mvhd')
    timescale = _qt_mvhd_timescale(movie, mvhd[0][2], mvhd[0][3])
    ticks = round(still_time_seconds * timescale)
    track = _live_photo_timing_track(still_time_movie_ticks=ticks, movie_timescale=timescale)
    return movie[:moov_start] + _qt_box(b'moov', movie[moov_payload:moov_end] + track)


def _build_live_photo_fixture(still_path: Path, movie_path: Path, timeout_seconds: float) -> dict:
    import uuid
    cid = str(uuid.uuid4()).upper()
    still_path.write_bytes(_live_photo_jpeg_bytes(cid))
    ffmpeg = _tool_identity('ffmpeg')
    cmd = [
        ffmpeg['path'], '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc=size=64x48:rate=6', '-t', '2', '-an',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30', '-pix_fmt', 'yuv420p',
        '-movflags', 'use_metadata_tags',
        '-metadata', f'com.apple.quicktime.content.identifier={cid}', str(movie_path),
    ]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as e:
        raise RelationshipQualificationError(f'ffmpeg Live Photo fixture generation timed out after {timeout_seconds:g}s') from e
    if cp.returncode or not movie_path.is_file():
        raise RelationshipQualificationError(cp.stderr.strip() or 'ffmpeg failed to generate Live Photo MOV fixture')
    raw = movie_path.read_bytes()
    mdat_before = mp4_mdat_fingerprint(raw)
    timed = _inject_live_photo_timing_track(raw, still_time_seconds=0.6)
    if mp4_mdat_fingerprint(timed) != mdat_before:
        raise RelationshipQualificationError('timing-track fixture construction changed MOV mdat')
    movie_path.write_bytes(timed)
    pair = inspect_live_photo_pair(still_path, movie_path, timeout_seconds=timeout_seconds)
    if not (pair.identity_verified and pair.component_health_verified and pair.timing_resource_verified):
        raise RelationshipQualificationError(f'generated Live Photo pair failed independent integrity checks: {pair.errors}')
    return {
        'content_identifier': cid,
        'still_generator': 'Pillow JPEG + minimal Apple iOS MakerNote',
        'movie_generator': ffmpeg,
        'movie_codec': 'H.264/AVC via libx264',
        'still_image_time_us': pair.still_image_time_us,
        'pair_integrity_schema': pair.schema,
        'apple_photos_acceptance_claimed': False,
    }


def _live_photo_readback(adapter: ExifToolAdapter, path: Path) -> tuple[bool, bool, bool]:
    try:
        tags = adapter.read(path)
    except Exception:
        return False, False, False
    expected_desc = 'GPA Live Photo relationship qualification fixture'
    desc = _find_suffix(tags, 'Description')
    desc_ok = desc == expected_desc or (isinstance(desc, list) and expected_desc in desc)
    date_created = _find_suffix(tags, 'DateCreated')
    date_ok = str(date_created or '').startswith('2017-05-01T21:30:00')
    return True, desc_ok, date_ok


def qualify_exiftool_live_photo(
    executable: Path,
    workdir: Path,
    *,
    distribution_root: Path | None = None,
    subprocess_timeout_seconds: float = 30.0,
) -> RelationshipQualificationReport:
    """Qualify one exact metadata writer against a genuine JPEG+MOV pair structure.

    This proves relationship preservation only in the disposable harness.  It does
    not claim that Apple Photos accepts the pair, and it never enables production
    writes by itself.
    """
    if subprocess_timeout_seconds <= 0:
        raise ValueError('subprocess_timeout_seconds must be > 0')
    workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    report = RelationshipQualificationReport(
        harness=LIVE_PHOTO_HARNESS_ID,
        relationship_profile_id=LIVE_PHOTO_PROFILE_ID,
        format_profile_id=JPEG_PROFILE_ID,
        secondary_format_profile_id=MOV_PROFILE_ID,
        created_utc=datetime.now(timezone.utc).isoformat(),
    )
    try:
        fp = fingerprint_exiftool_candidate(Path(executable), distribution_root)
        report.executable = fp['executable']; report.executable_sha256 = fp['executable_sha256']
        report.distribution_root = fp['distribution_root']; report.distribution_sha256 = fp['distribution_sha256']
        version = _candidate_version(Path(report.executable), subprocess_timeout_seconds)
        report.version = version
        report.qualification_id = _qualification_id(
            harness=LIVE_PHOTO_HARNESS_ID, relationship_profile_id=LIVE_PHOTO_PROFILE_ID,
            format_profile_id=JPEG_PROFILE_ID, secondary_format_profile_id=MOV_PROFILE_ID,
            version=version, executable_sha256=report.executable_sha256,
            distribution_sha256=report.distribution_sha256,
        )
        report.checks['version_nonempty'] = bool(version)
        report.checks['executable_fingerprinted'] = len(report.executable_sha256 or '') == 64
        if distribution_root is not None:
            report.checks['distribution_fingerprinted'] = len(report.distribution_sha256 or '') == 64

        still = workdir / 'qualification-live-photo.jpg'
        movie = workdir / 'qualification-live-photo.mov'
        report.fixture_generator = _build_live_photo_fixture(still, movie, subprocess_timeout_seconds)
        report.fixture_path = str(still)
        report.checks['fixture_created'] = still.is_file() and movie.is_file()
        before_still = still.read_bytes(); before_movie = movie.read_bytes()
        before_pair = inspect_live_photo_pair(still, movie, timeout_seconds=subprocess_timeout_seconds)
        report.observations['before_pair_integrity'] = before_pair.to_dict()
        report.checks['fixture_pair_identity_valid'] = before_pair.identity_verified
        report.checks['fixture_components_decodable'] = before_pair.component_health_verified
        report.checks['fixture_timing_resource_valid'] = before_pair.timing_resource_verified
        before_still_payload = jpeg_payload_fingerprint(before_still)
        before_mdat = mp4_mdat_fingerprint(before_movie)

        plan = MetadataPlan(xmp={
            'XMP-photoshop:DateCreated': '2017-05-01T21:30:00+09:00',
            'dc:description': 'GPA Live Photo relationship qualification fixture',
        })
        adapter = ExifToolAdapter(report.executable, approved_versions=(version,), timeout_seconds=subprocess_timeout_seconds)
        try:
            report.observations['still_write_output'] = adapter.write(still, plan)
            report.observations['movie_write_output'] = adapter.write(movie, plan)
            report.checks['write_completed'] = True
        except Exception as e:
            report.checks['write_completed'] = False
            report.errors.append(f'write failed: {e}')

        if still.is_file() and movie.is_file():
            after_still = still.read_bytes(); after_movie = movie.read_bytes()
            report.checks['still_file_changed'] = after_still != before_still
            report.checks['movie_file_changed'] = after_movie != before_movie
            try:
                after_pair = inspect_live_photo_pair(still, movie, timeout_seconds=subprocess_timeout_seconds)
                report.observations['after_pair_integrity'] = after_pair.to_dict()
                report.checks['relationship_structure_preserved'] = after_pair.identity_verified and after_pair.timing_resource_verified
                report.checks['content_identifier_preserved'] = (
                    after_pair.normalized_content_identifier == before_pair.normalized_content_identifier
                    and after_pair.normalized_content_identifier is not None
                )
                report.checks['timing_resource_preserved'] = (
                    after_pair.still_image_time_us == before_pair.still_image_time_us
                    and after_pair.timing_resource_verified
                )
                report.checks['still_payload_unchanged'] = jpeg_payload_fingerprint(after_still) == before_still_payload
                report.checks['video_mdat_unchanged'] = mp4_mdat_fingerprint(after_movie) == before_mdat
                # The MOV container may legitimately change when metadata is written;
                # unlike Motion Photo, the entire movie byte sequence is not required
                # to remain identical. Encoded media mdat and timing identity are.
                report.checks['video_bytes_unchanged'] = report.checks['video_mdat_unchanged']
                report.checks['still_decodable_after_write'] = after_pair.component_health_verified and after_pair.checks.get('still_media_valid') is True
                report.checks['video_decodable_after_write'] = after_pair.component_health_verified and after_pair.checks.get('movie_media_valid') is True
                report.checks['still_validator_fingerprinted'] = after_pair.checks.get('still_validator_fingerprinted') is True
                report.checks['video_validator_fingerprinted'] = after_pair.checks.get('movie_validator_fingerprinted') is True
            except Exception as e:
                for key in (
                    'relationship_structure_preserved','content_identifier_preserved','timing_resource_preserved',
                    'still_payload_unchanged','video_mdat_unchanged','video_bytes_unchanged',
                    'still_decodable_after_write','video_decodable_after_write',
                    'still_validator_fingerprinted','video_validator_fingerprinted',
                ):
                    report.checks[key] = False
                report.errors.append(f'relationship verification failed: {e}')
        else:
            report.checks['still_file_changed'] = False; report.checks['movie_file_changed'] = False
            report.errors.append('candidate removed a Live Photo qualification component')

        sr, sd, sdate = _live_photo_readback(adapter, still)
        mr, md, mdate = _live_photo_readback(adapter, movie)
        report.checks['readback_completed'] = sr and mr
        report.checks['readback_description'] = sd and md
        report.checks['readback_xmp_date'] = sdate and mdate
    except Exception as e:
        report.errors.append(str(e))

    required = {
        'version_nonempty','executable_fingerprinted','fixture_created','fixture_pair_identity_valid',
        'fixture_components_decodable','fixture_timing_resource_valid','write_completed',
        'still_file_changed','movie_file_changed','relationship_structure_preserved',
        'content_identifier_preserved','timing_resource_preserved','still_payload_unchanged',
        'video_mdat_unchanged','video_bytes_unchanged','still_decodable_after_write',
        'video_decodable_after_write','still_validator_fingerprinted','video_validator_fingerprinted',
        'readback_completed','readback_description','readback_xmp_date',
    }
    if distribution_root is not None:
        required.add('distribution_fingerprinted')
    report.passed = all(report.checks.get(key) is True for key in required) and not report.errors
    return report

def write_relationship_qualification_report(path: Path, report: RelationshipQualificationReport) -> Path:
    path = Path(path)
    write_json_new(path, report.to_dict())
    return path
