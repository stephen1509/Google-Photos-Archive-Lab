#!/usr/bin/env python3
"""Reproducible mixed JPEG+MP4 incremental/relocation stress qualification.

Creates a disposable synthetic Takeout containing genuinely decodable, byte-distinct
JPEG photos and MP4 videos, then runs three archive passes:
  1. initial import,
  2. identical re-import (must be unchanged/idempotent),
  3. same media bytes with corrected dates (must relocate without losing validation).

This is a lab qualification tool. It never reads a user's archive.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / 'src'))

from PIL import Image, __version__ as PILLOW_VERSION
from gpa.audit import audit_archive
from gpa.executor import RunExecutor
from gpa.revisions import RevisionStore

SCHEMA = 'gpa.stress-mixed-incremental.v1'


def _jpeg(i: int) -> bytes:
    # Width changes above 255 so larger runs cannot repeat just because the
    # deterministic RGB cycle wraps.
    w = 48 + (i // 256)
    h = 32
    im = Image.new('RGB', (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 7 + i) % 256, (y * 11 + i * 3) % 256, (x + y + i * 13) % 256)
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=88, subsampling=0)
    return b.getvalue()


def _tool_version(exe: str, marker: str) -> str | None:
    try:
        cp = subprocess.run([exe, '-version'], capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        return None
    line = ((cp.stdout or cp.stderr or '').splitlines() or [''])[0].strip()
    if marker in line:
        tail = line.split(marker, 1)[1].strip()
        return tail.split()[0] if tail else line
    return line or None


def _mp4(i: int, ffmpeg: str) -> bytes:
    # Tiny valid real video. Color + metadata both vary by i, giving distinct
    # container bytes while keeping generation inexpensive.
    r = (i * 53 + 17) % 256
    g = (i * 97 + 29) % 256
    b = (i * 193 + 43) % 256
    color = f'0x{r:02x}{g:02x}{b:02x}'
    with tempfile.TemporaryDirectory(prefix='gpa-mixed-video-') as td:
        out = Path(td) / 'fixture.mp4'
        cmd = [
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'lavfi', '-i', f'color=c={color}:s=32x32:r=5:d=0.4',
            '-c:v', 'mpeg4', '-q:v', '5', '-an',
            '-metadata', f'comment=gpa-mixed-stress-{i}',
            '-movflags', '+faststart', '-y', str(out),
        ]
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if cp.returncode != 0 or not out.exists():
            raise RuntimeError(f'ffmpeg fixture generation failed: {cp.stderr or cp.stdout}')
        return out.read_bytes()


def _write_takeout(
    path: Path,
    photo_count: int,
    video_count: int,
    start: datetime,
    description_prefix: str,
    *,
    ffmpeg: str,
    media: dict[str, bytes] | None = None,
) -> dict[str, bytes]:
    rows = {} if media is None else dict(media)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for i in range(photo_count):
            name = f'IMG_{i:06d}.jpg'
            logical = f'Photos from 2017/{name}'
            raw = rows.setdefault(logical, _jpeg(i))
            z.writestr(logical, raw)
            ts = int((start + timedelta(minutes=i)).timestamp())
            side = {
                'title': name,
                'photoTakenTime': {'timestamp': str(ts)},
                'description': f'{description_prefix} photo {i}',
            }
            z.writestr(logical + '.json', json.dumps(side, ensure_ascii=False))

        video_base = photo_count + 10_000
        for i in range(video_count):
            name = f'VID_{i:06d}.mp4'
            logical = f'Photos from 2017/{name}'
            if logical not in rows:
                rows[logical] = _mp4(video_base + i, ffmpeg)
            z.writestr(logical, rows[logical])
            ts = int((start + timedelta(minutes=photo_count + i)).timestamp())
            side = {
                'title': name,
                'photoTakenTime': {'timestamp': str(ts)},
                'description': f'{description_prefix} video {i}',
            }
            z.writestr(logical + '.json', json.dumps(side, ensure_ascii=False))
    return rows


def _actions(manifest: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in manifest.get('successes', []):
        k = row.get('action', 'unknown')
        out[k] = out.get(k, 0) + 1
    return out


def _run(ex: RunExecutor, run_id: str) -> tuple[dict, float]:
    t = time.perf_counter()
    m = ex.execute(run_id=run_id)
    return m, time.perf_counter() - t


def _validation_counts(root: Path) -> dict[str, int]:
    store = RevisionStore(root)
    out: dict[str, int] = {}
    assets = root / 'metadata' / 'assets'
    if not assets.exists():
        return out
    for d in assets.iterdir():
        if not d.is_dir():
            continue
        rec = store.current_record(d.name)
        if not rec:
            continue
        mv = rec.get('media_validation') or {}
        key = f"{mv.get('status', 'missing')}:{mv.get('validator') or 'none'}"
        out[key] = out.get(key, 0) + 1
    return out


def qualify(workdir: Path, photo_count: int, video_count: int) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        return {
            'schema': SCHEMA,
            'photo_count': photo_count,
            'video_count': video_count,
            'passed': False,
            'checks': {'ffmpeg_and_ffprobe_available': False},
            'environment': {'python': sys.version.split()[0], 'pillow': PILLOW_VERSION},
        }

    total = photo_count + video_count
    root = workdir / 'archive'
    initial = workdir / 'takeout_initial.zip'
    corrected = workdir / 'takeout_corrected.zip'
    media = _write_takeout(
        initial, photo_count, video_count,
        datetime(2017, 5, 15, 12, tzinfo=timezone.utc),
        'Initial mixed stress', ffmpeg=ffmpeg,
    )
    _write_takeout(
        corrected, photo_count, video_count,
        datetime(2017, 6, 15, 12, tzinfo=timezone.utc),
        'Corrected mixed stress', ffmpeg=ffmpeg, media=media,
    )

    m1, t1 = _run(RunExecutor([initial], root, storage_reserve_fraction=0.0), 'mixed_initial')
    a1 = audit_archive(root)
    m2, t2 = _run(RunExecutor([initial], root, storage_reserve_fraction=0.0), 'mixed_reimport')
    a2 = audit_archive(root)
    m3, t3 = _run(RunExecutor([corrected], root, storage_reserve_fraction=0.0), 'mixed_corrected')
    a3 = audit_archive(root)

    actions1 = _actions(m1)
    actions2 = _actions(m2)
    actions3 = _actions(m3)
    validators = _validation_counts(root)
    photo_key = 'passed:Pillow'
    video_key = 'passed:ffprobe'
    checks = {
        'ffmpeg_and_ffprobe_available': True,
        'initial_complete': m1.get('status') == 'complete',
        'initial_all_new': actions1 == {'new': total},
        'initial_audit_clean': a1.ok and a1.assets_checked == total and a1.media_validation_passed == total,
        'reimport_complete': m2.get('status') == 'complete',
        'reimport_all_unchanged': actions2 == {'unchanged': total},
        'reimport_no_new_revisions': a2.revisions_checked == total,
        'reimport_audit_clean': a2.ok and a2.media_validation_passed == total,
        'correction_complete': m3.get('status') == 'complete',
        'correction_all_relocated': actions3 == {'relocated': total},
        'correction_two_revisions_each': a3.revisions_checked == total * 2,
        'correction_validation_preserved': a3.media_validation_passed == total,
        'photo_validation_count': validators.get(photo_key, 0) == photo_count,
        'video_validation_count': validators.get(video_key, 0) == video_count,
        'final_audit_clean': a3.ok,
    }
    return {
        'schema': SCHEMA,
        'photo_count': photo_count,
        'video_count': video_count,
        'total_count': total,
        'passed': all(checks.values()),
        'checks': checks,
        'actions': {'initial': actions1, 'reimport': actions2, 'corrected': actions3},
        'seconds': {'initial': round(t1, 3), 'reimport': round(t2, 3), 'corrected': round(t3, 3)},
        'audit': {
            'assets': a3.assets_checked,
            'revisions': a3.revisions_checked,
            'validation_passed': a3.media_validation_passed,
            'problems': [vars(x) for x in a3.problems],
        },
        'validation_counts': validators,
        'environment': {
            'python': sys.version.split()[0],
            'pillow': PILLOW_VERSION,
            'ffmpeg': _tool_version(ffmpeg, 'ffmpeg version'),
            'ffprobe': _tool_version(ffprobe, 'ffprobe version'),
        },
        'artifacts': {
            'initial_zip_bytes': initial.stat().st_size,
            'corrected_zip_bytes': corrected.stat().st_size,
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--workdir', required=True, type=Path)
    ap.add_argument('--photos', type=int, default=100)
    ap.add_argument('--videos', type=int, default=20)
    ap.add_argument('--report', type=Path)
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args(argv)
    if a.photos < 0 or a.videos < 0 or a.photos + a.videos < 1:
        ap.error('counts must be >= 0 and total must be >= 1')
    if a.workdir.exists() and any(a.workdir.iterdir()):
        if not a.force:
            ap.error('workdir is not empty; use --force only for a disposable lab directory')
        shutil.rmtree(a.workdir)
    result = qualify(a.workdir, a.photos, a.videos)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        if a.report.exists():
            raise FileExistsError(a.report)
        a.report.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if result.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
