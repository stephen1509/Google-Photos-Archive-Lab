from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import subprocess
import uuid

import pytest
from PIL import Image

from gpa.live_photo_integrity import (
    LIVE_PHOTO_PAIR_INTEGRITY_SCHEMA,
    apple_content_identifier_from_jpeg_bytes,
    inspect_live_photo_pair,
)
from gpa.transactions import sha256_file


def _apple_makernote(cid: str) -> bytes:
    value = cid.encode('ascii') + b'\x00'
    header = b'Apple iOS\x00\x00\x01MM'
    value_offset = 14 + 2 + 12 + 4
    entry = (0x0011).to_bytes(2, 'big') + (2).to_bytes(2, 'big') + len(value).to_bytes(4, 'big') + value_offset.to_bytes(4, 'big')
    return header + (1).to_bytes(2, 'big') + entry + (0).to_bytes(4, 'big') + value


def _jpeg_with_apple_cid(cid: str) -> bytes:
    im = Image.new('RGB', (48, 32))
    pix = im.load()
    for y in range(im.height):
        for x in range(im.width):
            pix[x, y] = ((x*9+y*3)%256, (x*5+y*13)%256, (x*17+y*7)%256)
    out = BytesIO(); im.save(out, 'JPEG', quality=91)
    jpeg = out.getvalue()
    maker = _apple_makernote(cid)
    # TIFF MM: IFD0 at 8 -> ExifIFD at 26 -> MakerNote at 44.
    tiff_header = b'MM\x00*' + (8).to_bytes(4, 'big')
    ifd0 = (1).to_bytes(2, 'big') + (0x8769).to_bytes(2, 'big') + (4).to_bytes(2, 'big') + (1).to_bytes(4, 'big') + (26).to_bytes(4, 'big') + (0).to_bytes(4, 'big')
    maker_off = 44
    exif_ifd = (1).to_bytes(2, 'big') + (0x927C).to_bytes(2, 'big') + (7).to_bytes(2, 'big') + len(maker).to_bytes(4, 'big') + maker_off.to_bytes(4, 'big') + (0).to_bytes(4, 'big')
    payload = b'Exif\x00\x00' + tiff_header + ifd0 + exif_ifd + maker
    app1 = b'\xff\xe1' + (len(payload)+2).to_bytes(2, 'big') + payload
    return jpeg[:2] + app1 + jpeg[2:]


def _movie(path: Path, cid: str) -> Path:
    cp = subprocess.run([
        'ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','lavfi','-i','testsrc=size=64x48:rate=6','-t','0.5','-an',
        '-c:v','libx264','-preset','ultrafast','-crf','30','-pix_fmt','yuv420p',
        '-movflags','use_metadata_tags+faststart',
        '-metadata',f'com.apple.quicktime.content.identifier={cid}',str(path)
    ], capture_output=True, text=True, timeout=20)
    assert cp.returncode == 0, cp.stderr
    return path


def _pair(tmp_path: Path, cid: str | None = None):
    cid = cid or str(uuid.uuid4()).upper()
    still = tmp_path/'IMG_0001.JPG'; movie = tmp_path/'IMG_0001.MOV'
    still.write_bytes(_jpeg_with_apple_cid(cid)); _movie(movie, cid)
    return still, movie, cid


def test_independent_apple_makernote_content_identifier_parser():
    cid = str(uuid.uuid4()).upper()
    assert apple_content_identifier_from_jpeg_bytes(_jpeg_with_apple_cid(cid)) == cid


def test_live_photo_pair_integrity_verifies_matching_ids_and_media(tmp_path):
    still, movie, cid = _pair(tmp_path)
    before = (sha256_file(still), sha256_file(movie))
    r = inspect_live_photo_pair(still, movie)
    assert r.schema == LIVE_PHOTO_PAIR_INTEGRITY_SCHEMA
    assert r.identity_verified and r.component_health_verified
    assert r.normalized_content_identifier == str(uuid.UUID(cid))
    assert r.checks['content_identifiers_match']
    assert r.checks['movie_has_video_stream']
    assert r.checks['still_bytes_unchanged'] and r.checks['movie_bytes_unchanged']
    assert not r.timing_resource_verified
    assert r.checks['still_image_time_track_present'] is False
    assert not r.photos_usability_verified and not r.production_write_approved
    assert before == (sha256_file(still), sha256_file(movie))


def test_live_photo_pair_mismatched_ids_fail_identity_without_mutation(tmp_path):
    a = str(uuid.uuid4()).upper(); b = str(uuid.uuid4()).upper()
    still = tmp_path/'a.jpg'; movie = tmp_path/'a.mov'
    still.write_bytes(_jpeg_with_apple_cid(a)); _movie(movie, b)
    before = (sha256_file(still), sha256_file(movie))
    r = inspect_live_photo_pair(still, movie)
    assert not r.identity_verified
    assert r.checks['content_identifiers_match'] is False
    assert r.component_health_verified
    assert before == (sha256_file(still), sha256_file(movie))


def test_live_photo_pair_corrupt_movie_fails_health(tmp_path):
    still, movie, _ = _pair(tmp_path)
    movie.write_bytes(movie.read_bytes()[:80])
    r = inspect_live_photo_pair(still, movie)
    assert not r.component_health_verified
    assert not r.identity_verified or r.errors


def test_live_photo_pair_wrong_suffixes_fail_closed(tmp_path):
    still, movie, _ = _pair(tmp_path)
    bad_still = tmp_path/'still.png'; bad_still.write_bytes(still.read_bytes())
    r = inspect_live_photo_pair(bad_still, movie)
    assert not r.identity_verified and r.errors


def test_cli_inspect_live_photo_pair(tmp_path, capsys):
    from gpa.__main__ import main
    still, movie, _ = _pair(tmp_path)
    rc = main(['inspect-live-photo-pair','--still',str(still),'--movie',str(movie)])
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert obj['identity_verified'] is True and obj['component_health_verified'] is True
    assert obj['photos_usability_verified'] is False



def _heic_with_apple_cid(cid: str) -> bytes:
    # Reuse the pinned genuine libheif HEVC fixture, replacing only its existing
    # XMP metadata item with a standards-shaped Exif item describing the same
    # primary image. The HEVC protected item bytes remain byte-identical.
    from gpa.fingerprint import _box_spans, _fullbox, _parse_infe, _parse_iloc
    from gpa.live_photo_integrity import _find_exif_tiff
    source = (Path(__file__).parent / 'fixtures' / 'heic' / 'rainbow-451x461.heic').read_bytes()
    data = bytearray(source)
    meta = [r for r in _box_spans(source) if r[0] == b'meta'][0]
    _mv, _mf, child_start = _fullbox(source, meta[2], meta[3])
    children = list(_box_spans(source, child_start, meta[3]))
    iinf = [r for r in children if r[0] == b'iinf'][0]
    iv, _if, pos = _fullbox(source, iinf[2], iinf[3])
    pos += 2 if iv == 0 else 4
    changed = False
    for typ, _start, payload, end in _box_spans(source, pos, iinf[3]):
        assert typ == b'infe'
        item_id, item_type, _name, content_type = _parse_infe(source, payload, end)
        if item_id == 2 and item_type == b'mime' and content_type == b'application/rdf+xml':
            item_type_off = payload + 8  # fullbox + item_id + protection index
            data[item_type_off:item_type_off + 4] = b'Exif'
            room = end - (item_type_off + 4)
            data[item_type_off + 4:end] = b'E' * (room - 1) + b'\x00'
            changed = True
    assert changed
    iloc = [r for r in children if r[0] == b'iloc'][0]
    mdats = [(payload, end) for typ, _start, payload, end in _box_spans(source) if typ == b'mdat']
    locations = _parse_iloc(source, iloc[2], iloc[3], mdat_payloads=mdats, idat_payload=None)
    a, z = locations[2][0]
    tiff = _find_exif_tiff(_jpeg_with_apple_cid(cid))
    exif_block = (0).to_bytes(4, 'big') + tiff
    assert len(exif_block) <= z - a
    data[a:z] = exif_block + b'\x00' * (z - a - len(exif_block))
    return bytes(data)


def test_independent_heif_primary_exif_content_identifier_parser_preserves_hevc_payload():
    from gpa.live_photo_integrity import apple_content_identifier_from_heif_bytes
    from gpa.fingerprint import heif_item_payload_fingerprint
    cid = str(uuid.uuid4()).upper()
    source = (Path(__file__).parent / 'fixtures' / 'heic' / 'rainbow-451x461.heic').read_bytes()
    still = _heic_with_apple_cid(cid)
    assert apple_content_identifier_from_heif_bytes(still) == cid
    assert heif_item_payload_fingerprint(still) == heif_item_payload_fingerprint(source)


def test_heic_mov_live_photo_pair_identity_and_health_are_verified_read_only(tmp_path):
    cid = str(uuid.uuid4()).upper()
    still = tmp_path / 'IMG_1000.HEIC'; movie = tmp_path / 'IMG_1000.MOV'
    still.write_bytes(_heic_with_apple_cid(cid)); _movie(movie, cid)
    before = (sha256_file(still), sha256_file(movie))
    r = inspect_live_photo_pair(still, movie)
    assert r.identity_verified and r.component_health_verified
    assert r.observations['still_identifier_parser'] == 'heif-primary-exif-cdsc-apple-makernote-v1'
    assert r.still_validation['status'] == 'passed'
    assert not r.timing_resource_verified
    assert before == (sha256_file(still), sha256_file(movie))

def _box(typ: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, 'big') + typ + payload


def _fullbox(typ: bytes, payload: bytes, *, version: int = 0, flags: int = 0) -> bytes:
    return _box(typ, bytes([version]) + flags.to_bytes(3, 'big') + payload)


def _elst_entry(duration: int, media_time: int, *, version: int = 0) -> bytes:
    if version == 0:
        return duration.to_bytes(4, 'big') + media_time.to_bytes(4, 'big', signed=True) + b'\x00\x01\x00\x00'
    return duration.to_bytes(8, 'big') + media_time.to_bytes(8, 'big', signed=True) + b'\x00\x01\x00\x00'


def _timed_metadata_track(*, key: str = 'com.apple.quicktime.still-image-time', empty: int | None = 740,
                          samples: int = 1, elst_version: int = 0, nonleading_empty: bool = False) -> bytes:
    keyd = _box(b'keyd', b'mdta' + key.encode('utf-8'))
    keys = _box(b'keys', _box((1).to_bytes(4, 'big'), keyd))
    mebx = _box(b'mebx', b'\x00' * 8 + keys)
    stsd = _fullbox(b'stsd', (1).to_bytes(4, 'big') + mebx)
    stts = _fullbox(b'stts', (1).to_bytes(4, 'big') + samples.to_bytes(4, 'big') + (1).to_bytes(4, 'big'))
    stbl = _box(b'stbl', stsd + stts)
    minf = _box(b'minf', stbl)
    mdhd = _fullbox(b'mdhd', b'\x00' * 8 + (600).to_bytes(4, 'big') + (1).to_bytes(4, 'big'))
    hdlr = _fullbox(b'hdlr', b'\x00' * 4 + b'meta')
    mdia = _box(b'mdia', mdhd + hdlr + minf)
    edts = b''
    if empty is not None or nonleading_empty:
        rows = []
        if nonleading_empty:
            rows = [_elst_entry(1, 0, version=elst_version), _elst_entry(empty or 1, -1, version=elst_version)]
        elif empty:
            rows = [_elst_entry(empty, -1, version=elst_version), _elst_entry(1, 0, version=elst_version)]
        else:
            rows = [_elst_entry(1, 0, version=elst_version)]
        elst = _fullbox(b'elst', len(rows).to_bytes(4, 'big') + b''.join(rows), version=elst_version)
        edts = _box(b'edts', elst)
    return _box(b'trak', edts + mdia)


def _timing_mov(*tracks: bytes) -> bytes:
    mvhd = _fullbox(b'mvhd', b'\x00' * 8 + (600).to_bytes(4, 'big') + (1640).to_bytes(4, 'big'))
    return _box(b'ftyp', b'qt  ' + b'\x00\x00\x00\x00' + b'qt  ') + _box(b'moov', mvhd + b''.join(tracks))


def test_live_photo_timed_metadata_parser_matches_documented_edit_timing():
    from gpa.live_photo_integrity import apple_live_photo_still_image_time_from_mov_bytes, LIVE_PHOTO_TIMING_PARSER
    r = apple_live_photo_still_image_time_from_mov_bytes(_timing_mov(_timed_metadata_track(empty=740)))
    assert r['parser'] == LIVE_PHOTO_TIMING_PARSER
    assert r['movie_timescale'] == 600 and r['track_timescale'] == 600
    assert r['leading_empty_edit_duration'] == 740 and r['sample_count'] == 1
    assert r['still_image_time_us'] == 1_233_333
    assert r['still_image_time_seconds'] == pytest.approx(1.233333)


def test_live_photo_timed_metadata_parser_distinguishes_zero_from_absence():
    from gpa.live_photo_integrity import apple_live_photo_still_image_time_from_mov_bytes, LivePhotoIntegrityError
    zero = apple_live_photo_still_image_time_from_mov_bytes(_timing_mov(_timed_metadata_track(empty=None)))
    assert zero['still_image_time_us'] == 0
    with pytest.raises(LivePhotoIntegrityError, match='no unambiguous'):
        apple_live_photo_still_image_time_from_mov_bytes(_timing_mov(_timed_metadata_track(key='test.quicktime.foreign')))


def test_live_photo_timed_metadata_parser_supports_v1_edit_lists():
    from gpa.live_photo_integrity import apple_live_photo_still_image_time_from_mov_bytes
    r = apple_live_photo_still_image_time_from_mov_bytes(_timing_mov(_timed_metadata_track(empty=600, elst_version=1)))
    assert r['still_image_time_us'] == 1_000_000


def test_live_photo_timed_metadata_parser_rejects_multisample_and_nonleading_empty():
    from gpa.live_photo_integrity import apple_live_photo_still_image_time_from_mov_bytes, LivePhotoIntegrityError
    with pytest.raises(LivePhotoIntegrityError, match='exactly one sample'):
        apple_live_photo_still_image_time_from_mov_bytes(_timing_mov(_timed_metadata_track(samples=2)))
    with pytest.raises(LivePhotoIntegrityError, match='non-leading empty edit'):
        apple_live_photo_still_image_time_from_mov_bytes(_timing_mov(_timed_metadata_track(empty=600, nonleading_empty=True)))


def test_live_photo_timed_metadata_parser_rejects_ambiguous_duplicate_tracks():
    from gpa.live_photo_integrity import apple_live_photo_still_image_time_from_mov_bytes, LivePhotoIntegrityError
    track = _timed_metadata_track(empty=300)
    with pytest.raises(LivePhotoIntegrityError, match='multiple'):
        apple_live_photo_still_image_time_from_mov_bytes(_timing_mov(track, track))
