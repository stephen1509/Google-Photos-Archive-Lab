from __future__ import annotations

"""Read-only media validation with explicit validator provenance.

Validation is deliberately separate from source-byte integrity.  A corrupt image can
be copied and hashed perfectly; that proves faithful preservation of the source, not
that the media container is healthy.  These validators never rewrite the input.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import hashlib
import tempfile
from functools import lru_cache

from .formats import PHOTO_STANDARD_SUFFIXES, RAW_SUFFIXES, VIDEO_SUFFIXES
from .transactions import sha256_file
from .raw_validation import probe_raw_isolated


@dataclass(frozen=True)
class MediaValidation:
    status: str  # passed | failed | unavailable
    validator: str | None = None
    version: str | None = None
    validator_sha256: str | None = None
    media_sha256: str | None = None
    bytes_unchanged: bool | None = None
    detail: str = ""
    video_streams: int | None = None
    schema: str = "gpa.media-validation.v1"

    def to_dict(self) -> dict:
        return asdict(self)



@lru_cache(maxsize=32)
def _file_sha_if_present(path: str | None) -> str | None:
    if not path:
        return None
    try:
        p=Path(path).resolve()
        return sha256_file(p) if p.is_file() else None
    except Exception:
        return None


@lru_cache(maxsize=1)
def _pillow_build_sha256() -> str | None:
    """Fingerprint the exact Pillow code/native core used for validation.

    This is not a package-signature claim.  It is a reproducible local build
    fingerprint so a future audit can identify the exact implementation that
    made the validation decision.
    """
    try:
        import PIL
        from PIL import Image
        candidates=[]
        for obj in (PIL, Image, getattr(Image, 'core', None)):
            f=getattr(obj, '__file__', None)
            if f:
                pp=Path(f).resolve()
                if pp.is_file(): candidates.append(pp)
        rows=[]
        for pp in sorted(set(candidates), key=lambda x:str(x)):
            rows.append((pp.name, pp.stat().st_size, sha256_file(pp)))
        if not rows:return None
        h=hashlib.sha256()
        for name,size,digest in rows:
            h.update(name.encode('utf-8'));h.update(b'\0');h.update(str(size).encode());h.update(b'\0');h.update(digest.encode());h.update(b'\n')
        return h.hexdigest()
    except Exception:
        return None


def _with_integrity(result: MediaValidation, path: Path, expected_sha256: str | None, before_sha256: str | None) -> MediaValidation:
    try:
        after=sha256_file(path)
    except Exception as e:
        return MediaValidation('failed', result.validator, result.version, result.validator_sha256, None, False, f'could not hash media after validation: {e}', result.video_streams)
    reference=expected_sha256 or before_sha256
    unchanged=(reference is None or after==reference)
    if not unchanged:
        detail=(result.detail+'; ' if result.detail else '')+'media bytes changed during read-only validation'
        return MediaValidation('failed', result.validator, result.version, result.validator_sha256, after, False, detail, result.video_streams)
    return MediaValidation(result.status, result.validator, result.version, result.validator_sha256, after, True, result.detail, result.video_streams)

def _first_version_line(cmd: list[str]) -> str | None:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10, check=False)
    except Exception:
        return None
    text = (p.stdout or "").strip().splitlines()
    return text[0].strip() if text else None


def _ffprobe_version(exe: str) -> str | None:
    line = _first_version_line([exe, "-version"])
    if not line:
        return None
    m = re.search(r"ffprobe version\s+([^\s]+)", line, flags=re.I)
    return m.group(1) if m else line


def _libheif_version(exe: str) -> str | None:
    line = _first_version_line([exe, "--version"])
    if not line:
        return None
    # heif-info/heif-convert currently print a bare version such as 1.19.8 first.
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", line)
    return m.group(1) if m else line


def _validate_pillow(path: Path) -> MediaValidation:
    try:
        import PIL
        from PIL import Image
    except Exception as e:
        return MediaValidation('unavailable', 'Pillow', None, None, detail=f'Pillow unavailable: {e}')
    try:
        with Image.open(path) as im:
            im.verify()
        return MediaValidation('passed', 'Pillow', str(PIL.__version__), _pillow_build_sha256(), detail='container/image verification passed')
    except Exception as e:
        return MediaValidation('failed', 'Pillow', str(PIL.__version__), _pillow_build_sha256(), detail=str(e))


def _heif_convert_executable() -> tuple[str | None, str | None]:
    """Resolve the HEIF decoder, honoring an explicit qualification override.

    A configured path is deliberately fail-closed: falling back to a different
    executable would make a recorded toolchain identity misleading.
    """
    configured = os.environ.get("GPA_HEIF_CONVERT")
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate), None
        return None, f"GPA_HEIF_CONVERT does not name a regular file: {configured}"
    return shutil.which("heif-convert"), None


def _validate_heif(path: Path) -> MediaValidation:
    exe, resolution_error = _heif_convert_executable()
    if not exe:
        return MediaValidation(
            'unavailable', 'libheif/heif-convert + Pillow', None, None,
            detail=resolution_error or 'heif-convert is not installed',
        )
    version = _libheif_version(exe)
    converter_sha = _file_sha_if_present(exe)
    try:
        import PIL
        from PIL import Image
    except Exception as e:
        return MediaValidation('unavailable', 'libheif/heif-convert + Pillow', version, converter_sha, detail=f'Pillow unavailable for decoded-image verification: {e}')
    pillow_sha = _pillow_build_sha256()
    build_sha = None
    if converter_sha and pillow_sha:
        h=hashlib.sha256()
        h.update(b'heif-convert\0');h.update(converter_sha.encode('ascii'))
        h.update(b'\0Pillow\0');h.update(pillow_sha.encode('ascii'))
        build_sha=h.hexdigest()
    validator_version = f'libheif {version or "unknown"}; Pillow {PIL.__version__}'
    try:
        with tempfile.TemporaryDirectory(prefix='gpa-heif-decode-') as td:
            out = Path(td) / 'decoded.png'
            p = subprocess.run([exe, str(path), str(out)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60, check=False)
            if p.returncode != 0:
                detail = (p.stdout or '').strip()[-2000:] or f'heif-convert exited {p.returncode}'
                return MediaValidation('failed', 'libheif/heif-convert + Pillow', validator_version, build_sha, detail=detail)
            outputs = sorted(Path(td).glob('decoded*.png'))
            if not outputs:
                return MediaValidation('failed', 'libheif/heif-convert + Pillow', validator_version, build_sha, detail='heif-convert returned success but produced no decoded PNG')
            for decoded in outputs:
                with Image.open(decoded) as im:
                    im.verify()
            return MediaValidation('passed', 'libheif/heif-convert + Pillow', validator_version, build_sha, detail=f'decoded and independently verified {len(outputs)} HEIF/AVIF image(s)')
    except subprocess.TimeoutExpired:
        return MediaValidation('failed', 'libheif/heif-convert + Pillow', validator_version, build_sha, detail='heif-convert decode timed out after 60s')
    except Exception as e:
        return MediaValidation('failed', 'libheif/heif-convert + Pillow', validator_version, build_sha, detail=str(e))


def _validate_raw(path: Path) -> MediaValidation:
    probe = probe_raw_isolated(path)
    version = None
    if probe.rawpy_version or probe.libraw_version:
        version = f"rawpy {probe.rawpy_version or 'unknown'}; LibRaw {probe.libraw_version or 'unknown'}"
    detail = probe.detail
    if probe.status == 'passed':
        dims = f"decoded={probe.decoded_width}x{probe.decoded_height}x{probe.decoded_channels}"
        rawdims = f"raw={probe.raw_width}x{probe.raw_height}" if probe.raw_width and probe.raw_height else None
        visdims = f"visible={probe.visible_width}x{probe.visible_height}" if probe.visible_width and probe.visible_height else None
        extra = ', '.join(x for x in (rawdims, visdims, dims, f"raw_type={probe.raw_type}" if probe.raw_type else None) if x)
        detail = (detail + '; ' if detail else '') + extra
    return MediaValidation(probe.status, probe.validator, version, probe.validator_sha256, detail=detail)


def _validate_video(path: Path) -> MediaValidation:
    exe = shutil.which("ffprobe")
    if not exe:
        return MediaValidation('unavailable', 'ffprobe', None, None, detail='ffprobe is not installed')
    version = _ffprobe_version(exe)
    build_sha = _file_sha_if_present(exe)
    try:
        p = subprocess.run(
            [exe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=False,
        )
    except Exception as e:
        return MediaValidation('failed', 'ffprobe', version, build_sha, detail=str(e))
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip()[-2000:] or f"ffprobe exited {p.returncode}"
        return MediaValidation('failed', 'ffprobe', version, build_sha, detail=detail)
    try:
        obj = json.loads(p.stdout or "{}")
        nvideo = sum(1 for s in (obj.get("streams") or []) if s.get("codec_type") == "video")
    except Exception as e:
        return MediaValidation('failed', 'ffprobe', version, build_sha, detail=f'invalid ffprobe JSON: {e}')
    if nvideo < 1:
        return MediaValidation('failed', 'ffprobe', version, build_sha, detail='container parsed but no video stream was present', video_streams=0)
    return MediaValidation('passed', 'ffprobe', version, build_sha, detail='container parsed and video stream present', video_streams=nvideo)


def validate_media(path: str | Path, *, expected_sha256: str | None = None) -> MediaValidation:
    """Validate a preserved media file without modifying it.

    A successful result is bound to both the exact validator build fingerprint and
    the exact media SHA-256.  If ``expected_sha256`` is supplied (normal archive
    materialization), only the post-validation hash is needed.  Standalone calls
    hash before and after so a supposedly read-only validator cannot silently
    mutate the file.

    RAW formats are decoded in an isolated subprocess when rawpy/LibRaw is available.
    The exact rawpy/native build fingerprint and LibRaw version are recorded.  If the
    decoder is absent, RAW validation remains explicitly unavailable.  This is a
    read-only health check and does not authorize RAW metadata rewriting.  Unknown
    extensions also fail closed as unavailable rather than being misrepresented as
    healthy media.
    """
    path = Path(path)
    before=None
    if expected_sha256 is None:
        try:before=sha256_file(path)
        except Exception as e:return MediaValidation('failed', None, None, None, None, False, f'could not hash media before validation: {e}')
    suffix = path.suffix.casefold()
    if suffix in {'.heic', '.heif', '.avif'}:
        result=_validate_heif(path)
    elif suffix in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.tif', '.tiff'}:
        result=_validate_pillow(path)
    elif suffix in VIDEO_SUFFIXES:
        result=_validate_video(path)
    elif suffix in RAW_SUFFIXES:
        result=_validate_raw(path)
    elif suffix in PHOTO_STANDARD_SUFFIXES:
        result=MediaValidation('unavailable', None, None, None, detail=f'no qualified validator for {suffix}')
    else:
        result=MediaValidation('unavailable', None, None, None, detail=f'unrecognized media extension {suffix or "<none>"}')
    return _with_integrity(result,path,expected_sha256,before)
