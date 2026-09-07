from __future__ import annotations

"""Format-specific ExifTool round-trip qualification harnesses.

These harnesses are deliberately separate from the legacy JPEG build qualification.
They prove only a named media-family contract for one exact executable/distribution.
A passing report is evidence for review, never production approval by itself.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from .exiftool import ExifToolAdapter, executable_command
from .exiftool_qualification import HARNESS_ID as JPEG_HARNESS_ID, fingerprint_exiftool_candidate
from .fingerprint import jpeg_payload_fingerprint, png_visual_fingerprint, mp4_mdat_fingerprint, heif_item_payload_fingerprint
from .media_validation import validate_media
from .qualification_fixtures import HEIC_FIXTURE, verify_qualification_fixture
from .transactions import sha256_file, write_json_new
from .writepolicy import MetadataPlan

FORMAT_QUALIFICATION_SCHEMA = 'gpa.exiftool-format-qualification.v1'
PNG_HARNESS_ID = 'gpa.exiftool-png-roundtrip.v1'
QUICKTIME_HARNESS_ID = 'gpa.exiftool-quicktime-roundtrip.v1'
AVIF_HARNESS_ID = 'gpa.exiftool-avif-roundtrip.v1'
HEIC_HARNESS_ID = 'gpa.exiftool-heic-roundtrip.v1'


class FormatQualificationError(RuntimeError):
    pass


@dataclass
class FormatQualificationReport:
    schema: str = FORMAT_QUALIFICATION_SCHEMA
    harness: str = ''
    format_profile_id: str = ''
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
    fixture_admission: dict = field(default_factory=dict)
    validator: dict = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    observations: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized_exiftool_datetime(value: object) -> str:
    """Accept ExifTool's legacy date form without corrupting ISO-8601 readback."""
    text = str(value or '').strip()
    if len(text) >= 10 and text[4:5] == ':' and text[7:8] == ':':
        text = text[:4] + '-' + text[5:7] + '-' + text[8:]
    return text.replace(' ', 'T', 1)


def _candidate_version(executable: Path, timeout_seconds: float) -> str:
    try:
        cp = subprocess.run(executable_command(executable, '-ver'), capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as e:
        raise FormatQualificationError(f'ExifTool version check timed out after {timeout_seconds:g}s') from e
    if cp.returncode:
        raise FormatQualificationError(cp.stderr.strip() or 'ExifTool version check failed')
    version = cp.stdout.strip()
    if not version:
        raise FormatQualificationError('ExifTool returned an empty version')
    return version


def _find_suffix(tags: dict, suffix: str):
    if suffix in tags:
        return tags[suffix]
    for key, value in tags.items():
        if str(key).split(':')[-1] == suffix:
            return value
    return None


def _qualification_id(*, harness: str, profile: str, version: str, executable_sha256: str, distribution_sha256: str | None) -> str:
    payload = {
        'schema': FORMAT_QUALIFICATION_SCHEMA,
        'harness': harness,
        'format_profile_id': profile,
        'version': version,
        'executable_sha256': executable_sha256,
        'distribution_sha256': distribution_sha256,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def expected_format_qualification_id(report: dict) -> str:
    """Recompute the candidate/profile identity of a serialized format report."""
    return _qualification_id(
        harness=str(report.get('harness') or ''),
        profile=str(report.get('format_profile_id') or ''),
        version=str(report.get('version') or ''),
        executable_sha256=str(report.get('executable_sha256') or ''),
        distribution_sha256=report.get('distribution_sha256'),
    )



def _jpeg_fixture(path: Path) -> dict:
    import PIL
    from PIL import Image
    image = Image.new('RGB', (23, 19))
    pix = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pix[x, y] = ((x * 17 + y * 3) % 256, (x * 5 + y * 19) % 256, (x * 11 + y * 7) % 256)
    image.save(path, 'JPEG', quality=91, subsampling=0)
    return {'tool': 'Pillow JPEG encoder', 'version': str(PIL.__version__)}

def _pillow_fixture(path: Path) -> dict:
    import PIL
    from PIL import Image
    image = Image.new('RGBA', (19, 17))
    pix = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pix[x, y] = ((x * 29 + y * 7) % 256, (x * 11 + y * 31) % 256, (x * 17 + y * 13) % 256, 255)
    image.save(path, 'PNG', compress_level=6)
    return {'tool': 'Pillow', 'version': str(PIL.__version__)}


def _tool_identity(executable: str) -> dict:
    resolved = shutil.which(executable)
    if not resolved:
        raise FormatQualificationError(f'{executable} is not installed')
    p = Path(resolved).resolve()
    try:
        cp = subprocess.run(executable_command(p, '-version'), capture_output=True, text=True, timeout=10, check=False)
        first = (cp.stdout or cp.stderr or '').strip().splitlines()
        version_line = first[0] if first else None
    except Exception:
        version_line = None
    return {'tool': executable, 'path': str(p), 'sha256': sha256_file(p), 'version_line': version_line}


def _avif_fixture(path: Path) -> dict:
    import PIL
    from PIL import Image
    image = Image.new('RGB', (31, 23))
    pix = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pix[x, y] = ((x * 23 + y * 5) % 256, (x * 7 + y * 19) % 256, (x * 13 + y * 11) % 256)
    try:
        image.save(path, 'AVIF', quality=72)
    except Exception as e:
        raise FormatQualificationError(f'Pillow could not generate AVIF qualification fixture: {e}') from e
    return {'tool': 'Pillow AVIF encoder', 'version': str(PIL.__version__)}


def _quicktime_fixture(path: Path, timeout_seconds: float) -> dict:
    identity = _tool_identity('ffmpeg')
    cmd = [
        identity['path'], '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc=size=48x32:rate=4', '-t', '1',
        '-an', '-c:v', 'mpeg4', '-q:v', '4', '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart', str(path),
    ]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as e:
        raise FormatQualificationError(f'ffmpeg fixture generation timed out after {timeout_seconds:g}s') from e
    if cp.returncode or not path.is_file():
        raise FormatQualificationError(cp.stderr.strip() or 'ffmpeg failed to generate qualification fixture')
    return identity


def _format_contract(format_profile_id: str):
    if format_profile_id == 'jpeg-single-v1':
        return JPEG_HARNESS_ID, 'jpg', jpeg_payload_fingerprint, _jpeg_fixture
    if format_profile_id == 'png-single-v1':
        return PNG_HARNESS_ID, 'png', png_visual_fingerprint, _pillow_fixture
    if format_profile_id == 'mov-single-v1':
        return QUICKTIME_HARNESS_ID, 'mov', mp4_mdat_fingerprint, _quicktime_fixture
    if format_profile_id == 'mp4-single-v1':
        return QUICKTIME_HARNESS_ID, 'mp4', mp4_mdat_fingerprint, _quicktime_fixture
    if format_profile_id == 'm4v-single-v1':
        return QUICKTIME_HARNESS_ID, 'm4v', mp4_mdat_fingerprint, _quicktime_fixture
    if format_profile_id == 'avif-single-v1':
        return AVIF_HARNESS_ID, 'avif', heif_item_payload_fingerprint, _avif_fixture
    if format_profile_id == 'heic-single-v1':
        return HEIC_HARNESS_ID, 'heic', heif_item_payload_fingerprint, None
    raise ValueError(f'unsupported format qualification profile: {format_profile_id}')


def qualify_exiftool_format(
    executable: Path,
    workdir: Path,
    *,
    format_profile_id: str,
    distribution_root: Path | None = None,
    qualification_fixture: Path | None = None,
    subprocess_timeout_seconds: float = 30.0,
) -> FormatQualificationReport:
    if subprocess_timeout_seconds <= 0:
        raise ValueError('subprocess_timeout_seconds must be > 0')
    harness, suffix, payload_fingerprint, fixture_builder = _format_contract(format_profile_id)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    report = FormatQualificationReport(
        harness=harness,
        format_profile_id=format_profile_id,
        created_utc=datetime.now(timezone.utc).isoformat(),
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
            harness=harness, profile=format_profile_id, version=version,
            executable_sha256=report.executable_sha256,
            distribution_sha256=report.distribution_sha256,
        )
        report.checks['version_nonempty'] = bool(version)
        report.checks['executable_fingerprinted'] = len(report.executable_sha256 or '') == 64
        if distribution_root is not None:
            report.checks['distribution_fingerprinted'] = len(report.distribution_sha256 or '') == 64

        fixture = workdir / f'qualification.{suffix}'
        if format_profile_id == 'heic-single-v1':
            if qualification_fixture is None:
                raise FormatQualificationError(
                    'heic-single-v1 requires --fixture with the exact pinned HEIC qualification fixture'
                )
            admission = verify_qualification_fixture(Path(qualification_fixture), HEIC_FIXTURE)
            report.fixture_admission = admission
            report.fixture_generator = {
                'tool': 'pinned-external-fixture',
                'fixture_id': HEIC_FIXTURE.fixture_id,
                'source_sha256': admission['observed_sha256'],
                'license': admission['license'],
            }
            shutil.copyfile(Path(qualification_fixture), fixture)
            copied = verify_qualification_fixture(fixture, HEIC_FIXTURE)
            report.checks['fixture_exact_source_verified'] = admission['exact_bytes_verified'] is True
            report.checks['fixture_exact_copy_verified'] = copied['exact_bytes_verified'] is True
        elif suffix in {'mov','mp4','m4v'}:
            report.fixture_generator = fixture_builder(fixture, subprocess_timeout_seconds)
        else:
            report.fixture_generator = fixture_builder(fixture)
        report.fixture_path = str(fixture)
        report.checks['fixture_created'] = fixture.is_file()

        before = fixture.read_bytes()
        before_sha = _sha_bytes(before)
        before_payload = payload_fingerprint(before)
        before_validation = validate_media(fixture)
        report.checks['fixture_valid_before'] = before_validation.status == 'passed' and before_validation.bytes_unchanged is True
        report.observations['before_validation'] = before_validation.to_dict()

        plan = MetadataPlan(xmp={
            'XMP-photoshop:DateCreated': '2017-05-01T21:30:00+09:00',
            'dc:description': f'GPA {format_profile_id} qualification fixture',
        })
        adapter = ExifToolAdapter(report.executable, approved_versions=(version,), timeout_seconds=subprocess_timeout_seconds)
        try:
            report.observations['write_output'] = adapter.write(fixture, plan)
            report.checks['write_completed'] = True
        except Exception as e:
            report.checks['write_completed'] = False
            report.errors.append(f'write failed: {e}')

        if fixture.exists():
            after = fixture.read_bytes()
            after_sha = _sha_bytes(after)
            report.observations.update({'before_sha256': before_sha, 'after_sha256': after_sha})
            report.checks['whole_file_changed'] = after_sha != before_sha
            try:
                report.checks['media_payload_unchanged'] = payload_fingerprint(after) == before_payload
            except Exception as e:
                report.checks['media_payload_unchanged'] = False
                report.errors.append(f'payload fingerprint failed: {e}')
            after_validation = validate_media(fixture)
            report.validator = {
                'validator': after_validation.validator,
                'version': after_validation.version,
                'validator_sha256': after_validation.validator_sha256,
            }
            report.observations['after_validation'] = after_validation.to_dict()
            report.checks['output_decodable'] = after_validation.status == 'passed' and after_validation.bytes_unchanged is True
            report.checks['validator_identified'] = bool(after_validation.validator and after_validation.version)
            report.checks['validator_build_fingerprinted'] = isinstance(after_validation.validator_sha256, str) and len(after_validation.validator_sha256) == 64
        else:
            for key in ('whole_file_changed', 'media_payload_unchanged', 'output_decodable', 'validator_identified', 'validator_build_fingerprinted'):
                report.checks[key] = False
            report.errors.append('candidate removed qualification fixture')

        try:
            tags = adapter.read(fixture)
            report.checks['readback_completed'] = True
            report.observations['readback_keys'] = sorted(str(k) for k in tags.keys())
            desc = _find_suffix(tags, 'Description')
            expected_desc = f'GPA {format_profile_id} qualification fixture'
            report.checks['readback_description'] = desc == expected_desc or (isinstance(desc, list) and expected_desc in desc)
            date_created = _find_suffix(tags, 'DateCreated')
            report.checks['readback_xmp_date'] = _normalized_exiftool_datetime(date_created).startswith('2017-05-01T21:30:00')
        except Exception as e:
            report.checks['readback_completed'] = False
            report.checks['readback_description'] = False
            report.checks['readback_xmp_date'] = False
            report.errors.append(f'readback failed: {e}')
    except Exception as e:
        report.errors.append(str(e))

    required = {
        'version_nonempty', 'executable_fingerprinted', 'fixture_created', 'fixture_valid_before',
        'write_completed', 'whole_file_changed', 'media_payload_unchanged', 'output_decodable',
        'validator_identified', 'validator_build_fingerprinted', 'readback_completed',
        'readback_description', 'readback_xmp_date',
    }
    if distribution_root is not None:
        required.add('distribution_fingerprinted')
    if format_profile_id == 'heic-single-v1':
        required.update({'fixture_exact_source_verified', 'fixture_exact_copy_verified'})
    report.passed = all(report.checks.get(key) is True for key in required)
    return report


def write_format_qualification_report(path: Path, report: FormatQualificationReport) -> Path:
    path = Path(path)
    write_json_new(path, report.to_dict())
    return path
