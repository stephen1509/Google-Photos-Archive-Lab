from __future__ import annotations

"""Independent, read-only integrity evidence for Apple Live Photo pairs.

Apple documents Live Photos as a still image and movie associated by the same
content identifier: the movie stores AVMetadataQuickTimeMetadataKeyContentIdentifier,
while the still stores the corresponding identifier in its EXIF MakerNote.

This module intentionally does *not* claim full Apple Photos usability.  A genuine
Live Photo movie also carries timing/relationship details that require a separate
qualification lane.  The purpose here is narrower and archival: prove, without
mutating either source, that a JPEG still and MOV movie are healthy media and share
one independently parsed Apple content identifier.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import shutil
import subprocess
import uuid

from .media_validation import validate_media
from .transactions import sha256_file
from .fingerprint import _box_spans as _heif_box_spans, _fullbox as _heif_fullbox, _parse_iinf as _heif_parse_iinf, _parse_iloc as _heif_parse_iloc

LIVE_PHOTO_PAIR_INTEGRITY_SCHEMA = "gpa.live-photo-pair-integrity.v2"
LIVE_PHOTO_PAIR_PROFILE = "jpeg-mov-content-identifier-timing-v2"
LIVE_PHOTO_TIMING_PARSER = "gpa.apple-live-photo-timed-metadata-parser.v1"
_STILL_IMAGE_TIME_KEY = "com.apple.quicktime.still-image-time"


class LivePhotoIntegrityError(RuntimeError):
    pass


@dataclass
class LivePhotoPairIntegrity:
    schema: str = LIVE_PHOTO_PAIR_INTEGRITY_SCHEMA
    profile: str = LIVE_PHOTO_PAIR_PROFILE
    still_path: str = ""
    movie_path: str = ""
    still_sha256: str | None = None
    movie_sha256: str | None = None
    still_content_identifier: str | None = None
    movie_content_identifier: str | None = None
    normalized_content_identifier: str | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    still_validation: dict = field(default_factory=dict)
    movie_validation: dict = field(default_factory=dict)
    observations: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    identity_verified: bool = False
    component_health_verified: bool = False
    timing_resource_verified: bool = False
    still_image_time_us: int | None = None
    still_image_time_seconds: float | None = None
    photos_usability_verified: bool = False
    production_write_approved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _u16(data: bytes, off: int, endian: str) -> int:
    if off < 0 or off + 2 > len(data):
        raise LivePhotoIntegrityError("truncated TIFF uint16")
    return int.from_bytes(data[off:off + 2], endian)


def _u32(data: bytes, off: int, endian: str) -> int:
    if off < 0 or off + 4 > len(data):
        raise LivePhotoIntegrityError("truncated TIFF uint32")
    return int.from_bytes(data[off:off + 4], endian)


_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def _ifd_entries(data: bytes, offset: int, endian: str) -> list[tuple[int, int, int, int, bytes]]:
    count = _u16(data, offset, endian)
    if count > 4096:
        raise LivePhotoIntegrityError("implausible TIFF IFD entry count")
    base = offset + 2
    end = base + count * 12
    if end + 4 > len(data):
        raise LivePhotoIntegrityError("truncated TIFF IFD")
    rows = []
    for i in range(count):
        p = base + i * 12
        tag = _u16(data, p, endian)
        typ = _u16(data, p + 2, endian)
        n = _u32(data, p + 4, endian)
        raw4 = data[p + 8:p + 12]
        value_or_offset = int.from_bytes(raw4, endian)
        rows.append((tag, typ, n, value_or_offset, raw4))
    return rows


def _entry_bytes(data: bytes, entry: tuple[int, int, int, int, bytes], endian: str) -> bytes:
    _tag, typ, count, value_or_offset, raw4 = entry
    unit = _TYPE_SIZES.get(typ)
    if unit is None:
        raise LivePhotoIntegrityError(f"unsupported TIFF field type {typ}")
    total = unit * count
    if total > 16 * 1024 * 1024:
        raise LivePhotoIntegrityError("implausibly large TIFF field")
    if total <= 4:
        return raw4[:total]
    if value_or_offset < 0 or value_or_offset + total > len(data):
        raise LivePhotoIntegrityError("TIFF field offset escapes containing data")
    return data[value_or_offset:value_or_offset + total]


def _find_exif_tiff(jpeg: bytes) -> bytes:
    if not jpeg.startswith(b"\xff\xd8"):
        raise LivePhotoIntegrityError("still is not a JPEG")
    p = 2
    while p + 4 <= len(jpeg):
        if jpeg[p] != 0xFF:
            raise LivePhotoIntegrityError("invalid JPEG marker stream before SOS")
        while p < len(jpeg) and jpeg[p] == 0xFF:
            p += 1
        if p >= len(jpeg):
            break
        marker = jpeg[p]
        p += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        if marker == 0xDA:  # start of scan; EXIF APP1 must already have appeared
            break
        if p + 2 > len(jpeg):
            raise LivePhotoIntegrityError("truncated JPEG segment length")
        seglen = int.from_bytes(jpeg[p:p + 2], "big")
        if seglen < 2 or p + seglen > len(jpeg):
            raise LivePhotoIntegrityError("invalid JPEG segment length")
        payload = jpeg[p + 2:p + seglen]
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            return payload[6:]
        p += seglen
    raise LivePhotoIntegrityError("JPEG has no EXIF APP1 segment")


def _apple_content_identifier_from_tiff(tiff: bytes) -> str:
    if len(tiff) < 8 or tiff[2:4] != b"\x00*":
        raise LivePhotoIntegrityError("unsupported or invalid TIFF header")
    if tiff[:2] == b"MM":
        endian = "big"
    elif tiff[:2] == b"II":
        endian = "little"
    else:
        raise LivePhotoIntegrityError("invalid TIFF byte order")
    ifd0_off = _u32(tiff, 4, endian)
    ifd0 = _ifd_entries(tiff, ifd0_off, endian)
    exif_ptrs = [r for r in ifd0 if r[0] == 0x8769 and r[1] == 4 and r[2] == 1]
    if len(exif_ptrs) != 1:
        raise LivePhotoIntegrityError("image does not contain one unambiguous ExifIFD pointer")
    exif_ifd_off = exif_ptrs[0][3]
    exif_ifd = _ifd_entries(tiff, exif_ifd_off, endian)
    maker_rows = [r for r in exif_ifd if r[0] == 0x927C]
    if len(maker_rows) != 1:
        raise LivePhotoIntegrityError("image does not contain one unambiguous MakerNote")
    maker = _entry_bytes(tiff, maker_rows[0], endian)
    if len(maker) < 20 or not maker.startswith(b"Apple iOS\x00"):
        raise LivePhotoIntegrityError("MakerNote is not an Apple iOS MakerNote")
    if maker[12:14] != b"MM":
        raise LivePhotoIntegrityError("unsupported Apple MakerNote byte order")
    rows = _ifd_entries(maker, 14, "big")
    cid_rows = [r for r in rows if r[0] == 0x0011 and r[1] == 2]
    if len(cid_rows) != 1:
        raise LivePhotoIntegrityError("Apple MakerNote does not contain one ContentIdentifier")
    raw = _entry_bytes(maker, cid_rows[0], "big")
    try:
        value = raw.rstrip(b"\x00").decode("utf-8", "strict").strip()
    except UnicodeDecodeError as e:
        raise LivePhotoIntegrityError("Apple ContentIdentifier is not valid UTF-8/ASCII") from e
    if not value:
        raise LivePhotoIntegrityError("Apple ContentIdentifier is empty")
    return value


def apple_content_identifier_from_jpeg_bytes(jpeg: bytes) -> str:
    """Parse Apple MakerNote tag 0x0011 from a JPEG without ExifTool."""
    return _apple_content_identifier_from_tiff(_find_exif_tiff(jpeg))


def _heif_primary_item_and_metadata(data: bytes):
    top = list(_heif_box_spans(data))
    metas = [row for row in top if row[0] == b"meta"]
    if len(metas) != 1:
        raise LivePhotoIntegrityError("HEIF must contain exactly one file-level meta box")
    _typ, _meta_start, meta_payload, meta_end = metas[0]
    _version, _flags, child_start = _heif_fullbox(data, meta_payload, meta_end)
    children = list(_heif_box_spans(data, child_start, meta_end))
    by_type: dict[bytes, list[tuple]] = {}
    for row in children:
        by_type.setdefault(row[0], []).append(row)
    for required in (b"pitm", b"iinf", b"iloc"):
        if len(by_type.get(required, [])) != 1:
            raise LivePhotoIntegrityError(f"HEIF requires exactly one {required.decode('latin1')} box")
    pitm = by_type[b"pitm"][0]
    pv, _pf, pp = _heif_fullbox(data, pitm[2], pitm[3])
    width = 2 if pv == 0 else 4 if pv == 1 else 0
    if not width or pp + width != pitm[3]:
        raise LivePhotoIntegrityError("unsupported or malformed HEIF pitm")
    primary_id = int.from_bytes(data[pp:pp + width], "big")
    if primary_id <= 0:
        raise LivePhotoIntegrityError("invalid HEIF primary item id")
    iinf = by_type[b"iinf"][0]
    items = _heif_parse_iinf(data, iinf[2], iinf[3])
    iloc = by_type[b"iloc"][0]
    idats = by_type.get(b"idat", [])
    if len(idats) > 1:
        raise LivePhotoIntegrityError("multiple HEIF idat boxes are unsupported")
    mdat_payloads = [(payload, end) for typ, _start, payload, end in top if typ == b"mdat"]
    locations = _heif_parse_iloc(
        data, iloc[2], iloc[3], mdat_payloads=mdat_payloads,
        idat_payload=(idats[0][2], idats[0][3]) if idats else None,
    )
    if primary_id not in items:
        raise LivePhotoIntegrityError("HEIF primary item is missing from iinf")
    return primary_id, items, locations, by_type


def _heif_cdsc_references(data: bytes, iref_box: tuple | None) -> dict[int, set[int]]:
    if iref_box is None:
        return {}
    version, _flags, pos = _heif_fullbox(data, iref_box[2], iref_box[3])
    width = 2 if version == 0 else 4 if version == 1 else 0
    if not width:
        raise LivePhotoIntegrityError("unsupported HEIF iref version")
    out: dict[int, set[int]] = {}
    for typ, _start, payload, end in _heif_box_spans(data, pos, iref_box[3]):
        if typ != b"cdsc":
            continue
        if payload + width + 2 > end:
            raise LivePhotoIntegrityError("truncated HEIF cdsc reference")
        from_id = int.from_bytes(data[payload:payload + width], "big")
        payload += width
        count = int.from_bytes(data[payload:payload + 2], "big")
        payload += 2
        if count <= 0 or count > 4096 or payload + count * width != end:
            raise LivePhotoIntegrityError("malformed HEIF cdsc reference")
        targets = {int.from_bytes(data[payload + i * width:payload + (i + 1) * width], "big") for i in range(count)}
        if from_id in out:
            raise LivePhotoIntegrityError("duplicate HEIF cdsc source item")
        out[from_id] = targets
    return out


def apple_content_identifier_from_heif_bytes(data: bytes) -> str:
    """Parse the Apple MakerNote ContentIdentifier from primary-image HEIF Exif.

    HEIF Annex A stores Exif as an item whose first four bytes are a big-endian
    offset from the start of ``exif_payload`` to the TIFF header.  Metadata items
    describing the primary image are selected through a ``cdsc`` item reference,
    so an auxiliary image's Exif cannot be mistaken for the Live Photo still.
    """
    primary_id, items, locations, by_type = _heif_primary_item_and_metadata(data)
    irefs = by_type.get(b"iref", [])
    if len(irefs) > 1:
        raise LivePhotoIntegrityError("multiple HEIF iref boxes are unsupported")
    refs = _heif_cdsc_references(data, irefs[0] if irefs else None)
    candidates = [
        item_id for item_id, (item_type, _name, _content_type) in items.items()
        if item_type == b"Exif" and primary_id in refs.get(item_id, set())
    ]
    if len(candidates) != 1:
        raise LivePhotoIntegrityError("HEIF does not contain one Exif item describing the primary image")
    item_id = candidates[0]
    ranges = locations.get(item_id)
    if not ranges:
        raise LivePhotoIntegrityError("HEIF primary Exif item has no payload location")
    raw = b"".join(data[a:b] for a, b in ranges)
    if len(raw) < 12:
        raise LivePhotoIntegrityError("HEIF ExifDataBlock is too small")
    offset = int.from_bytes(raw[:4], "big")
    tiff_start = 4 + offset
    if tiff_start < 4 or tiff_start + 8 > len(raw):
        raise LivePhotoIntegrityError("HEIF Exif TIFF-header offset is invalid")
    return _apple_content_identifier_from_tiff(raw[tiff_start:])

def _normalize_identifier(value: str) -> str:
    try:
        return str(uuid.UUID(value.strip()))
    except Exception as e:
        raise LivePhotoIntegrityError("Live Photo ContentIdentifier is not a UUID") from e



# QuickTime timed-metadata parsing -------------------------------------------------
#
# Apple exposes the Live Photo still moment through a single-sample metadata track
# whose sample description is ``mebx`` and declares the mdta key
# ``com.apple.quicktime.still-image-time``.  The marker value itself is not the
# time; the presentation start of that track is.  A leading empty edit in ``elst``
# delays the one-sample track to the still moment.  This parser deliberately reads
# only ``moov`` metadata boxes and never follows sample offsets into ``mdat``.


def _qt_box_spans(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                raise LivePhotoIntegrityError("truncated QuickTime largesize box")
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or size > (1 << 63) or pos + size > end:
            raise LivePhotoIntegrityError("invalid QuickTime box size")
        yield typ, pos, pos + header, pos + size
        pos += size
    if pos != end:
        raise LivePhotoIntegrityError("trailing bytes in QuickTime box container")


def _qt_children(data: bytes, start: int, end: int, typ: bytes) -> list[tuple[bytes, int, int, int]]:
    return [row for row in _qt_box_spans(data, start, end) if row[0] == typ]


def _qt_one_child(data: bytes, start: int, end: int, typ: bytes, *, required: bool = True):
    rows = _qt_children(data, start, end, typ)
    if len(rows) > 1 or (required and len(rows) != 1):
        label = typ.decode("latin1", "replace")
        raise LivePhotoIntegrityError(f"expected {'one' if required else 'at most one'} {label} box")
    return rows[0] if rows else None


def _qt_fullbox(data: bytes, payload: int, end: int) -> tuple[int, int, int]:
    if payload + 4 > end:
        raise LivePhotoIntegrityError("truncated QuickTime full box")
    return data[payload], int.from_bytes(data[payload + 1:payload + 4], "big"), payload + 4


def _qt_mvhd_timescale(data: bytes, payload: int, end: int) -> int:
    version, _flags, pos = _qt_fullbox(data, payload, end)
    if version == 0:
        pos += 8
    elif version == 1:
        pos += 16
    else:
        raise LivePhotoIntegrityError("unsupported mvhd version")
    if pos + 4 > end:
        raise LivePhotoIntegrityError("truncated mvhd timescale")
    timescale = int.from_bytes(data[pos:pos + 4], "big")
    if timescale <= 0:
        raise LivePhotoIntegrityError("invalid movie timescale")
    return timescale


def _qt_mdhd_timescale(data: bytes, payload: int, end: int) -> int:
    version, _flags, pos = _qt_fullbox(data, payload, end)
    if version == 0:
        pos += 8
    elif version == 1:
        pos += 16
    else:
        raise LivePhotoIntegrityError("unsupported mdhd version")
    if pos + 4 > end:
        raise LivePhotoIntegrityError("truncated mdhd timescale")
    timescale = int.from_bytes(data[pos:pos + 4], "big")
    if timescale <= 0:
        raise LivePhotoIntegrityError("invalid metadata-track timescale")
    return timescale


def _qt_handler_type(data: bytes, payload: int, end: int) -> bytes:
    _version, _flags, pos = _qt_fullbox(data, payload, end)
    # pre_defined (4) followed by handler_type (4)
    if pos + 8 > end:
        raise LivePhotoIntegrityError("truncated hdlr box")
    return data[pos + 4:pos + 8]


def _qt_stts_sample_count(data: bytes, payload: int, end: int) -> int:
    version, _flags, pos = _qt_fullbox(data, payload, end)
    if version != 0 or pos + 4 > end:
        raise LivePhotoIntegrityError("unsupported or truncated stts box")
    entries = int.from_bytes(data[pos:pos + 4], "big")
    pos += 4
    if entries > 1_000_000 or pos + entries * 8 != end:
        raise LivePhotoIntegrityError("invalid stts entry table")
    total = 0
    for _ in range(entries):
        count = int.from_bytes(data[pos:pos + 4], "big")
        delta = int.from_bytes(data[pos + 4:pos + 8], "big")
        pos += 8
        if count <= 0 or delta <= 0:
            raise LivePhotoIntegrityError("invalid stts sample entry")
        total += count
        if total > 1_000_000_000:
            raise LivePhotoIntegrityError("implausible stts sample count")
    return total


def _qt_elst_leading_empty_duration(data: bytes, payload: int, end: int) -> int:
    version, _flags, pos = _qt_fullbox(data, payload, end)
    if pos + 4 > end:
        raise LivePhotoIntegrityError("truncated elst entry count")
    count = int.from_bytes(data[pos:pos + 4], "big")
    pos += 4
    if count > 4096:
        raise LivePhotoIntegrityError("implausible elst entry count")
    width = 12 if version == 0 else 20 if version == 1 else 0
    if not width or pos + count * width != end:
        raise LivePhotoIntegrityError("unsupported or malformed elst")
    entries: list[tuple[int, int]] = []
    for _ in range(count):
        if version == 0:
            duration = int.from_bytes(data[pos:pos + 4], "big")
            media_time = int.from_bytes(data[pos + 4:pos + 8], "big", signed=True)
            rate_i = int.from_bytes(data[pos + 8:pos + 10], "big", signed=True)
            rate_f = int.from_bytes(data[pos + 10:pos + 12], "big", signed=True)
        else:
            duration = int.from_bytes(data[pos:pos + 8], "big")
            media_time = int.from_bytes(data[pos + 8:pos + 16], "big", signed=True)
            rate_i = int.from_bytes(data[pos + 16:pos + 18], "big", signed=True)
            rate_f = int.from_bytes(data[pos + 18:pos + 20], "big", signed=True)
        pos += width
        if rate_i != 1 or rate_f != 0:
            raise LivePhotoIntegrityError("unsupported Live Photo edit rate")
        entries.append((duration, media_time))
    # A leading empty edit delays the metadata sample.  Empty edits later in the
    # list are not a valid unambiguous presentation-start signal for this lane.
    if any(media_time == -1 for _duration, media_time in entries[1:]):
        raise LivePhotoIntegrityError("non-leading empty edit in Live Photo metadata track")
    return entries[0][0] if entries and entries[0][1] == -1 else 0


def _qt_mebx_keys(data: bytes, entry_payload: int, entry_end: int) -> list[tuple[bytes, str]]:
    # ISO boxed metadata sample entries carry eight reserved/data-reference bytes
    # before their child boxes in Apple/Tika Live Photo files.  Fail closed rather
    # than scanning arbitrary payload bytes for a coincidental 'keys' fourcc.
    child_start = entry_payload + 8
    if child_start > entry_end:
        raise LivePhotoIntegrityError("truncated mebx sample entry")
    keys_boxes = _qt_children(data, child_start, entry_end, b"keys")
    if len(keys_boxes) != 1:
        raise LivePhotoIntegrityError("mebx sample entry does not contain one keys box")
    _typ, _start, payload, end = keys_boxes[0]
    # QuickTime's mebx keys box contains one child per local key ID.  The child's
    # four-byte type is the numeric local key ID (for example 0x00000001), and
    # its payload contains one 'keyd' declaration box.
    rows = list(_qt_box_spans(data, payload, end))
    if not rows or len(rows) > 4096:
        raise LivePhotoIntegrityError("invalid mebx key table")
    out: list[tuple[bytes, str]] = []
    seen_ids: set[bytes] = set()
    for key_id, _start, key_payload, key_end in rows:
        if key_id in seen_ids or int.from_bytes(key_id, "big") == 0:
            raise LivePhotoIntegrityError("invalid or duplicate mebx local key id")
        seen_ids.add(key_id)
        declarations = list(_qt_box_spans(data, key_payload, key_end))
        if len(declarations) != 1 or declarations[0][0] != b"keyd":
            raise LivePhotoIntegrityError("invalid mebx key declaration container")
        _decl_type, _decl_start, decl_payload, decl_end = declarations[0]
        if decl_payload + 4 > decl_end:
            raise LivePhotoIntegrityError("truncated mebx key declaration")
        namespace = data[decl_payload:decl_payload + 4]
        raw_name = data[decl_payload + 4:decl_end]
        try:
            name = raw_name.decode("utf-8", "strict")
        except UnicodeDecodeError as e:
            raise LivePhotoIntegrityError("mebx key name is not UTF-8") from e
        if not name or "\x00" in name:
            raise LivePhotoIntegrityError("invalid mebx key name")
        out.append((namespace, name))
    return out

def _qt_stsd_declares_still_image_time(data: bytes, payload: int, end: int) -> bool:
    version, _flags, pos = _qt_fullbox(data, payload, end)
    if version != 0 or pos + 4 > end:
        raise LivePhotoIntegrityError("unsupported or truncated stsd box")
    count = int.from_bytes(data[pos:pos + 4], "big")
    pos += 4
    if count > 4096:
        raise LivePhotoIntegrityError("implausible stsd entry count")
    seen = False
    for _ in range(count):
        if pos + 8 > end:
            raise LivePhotoIntegrityError("truncated stsd entry")
        size = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        if size < 8 or pos + size > end:
            raise LivePhotoIntegrityError("invalid stsd entry size")
        if typ == b"mebx":
            keys = _qt_mebx_keys(data, pos + 8, pos + size)
            if (b"mdta", _STILL_IMAGE_TIME_KEY) in keys:
                if seen:
                    raise LivePhotoIntegrityError("duplicate still-image-time mebx sample entry")
                seen = True
        pos += size
    if pos != end:
        raise LivePhotoIntegrityError("trailing stsd bytes")
    return seen


def apple_live_photo_still_image_time_from_mov_bytes(data: bytes) -> dict:
    """Return independent still-image-time evidence from a QuickTime MOV.

    The returned microsecond value is the presentation start of the unique
    single-sample ``mebx`` track declaring Apple's still-image-time key.  A value
    of zero is meaningful (the still is the first frame); absence is an error.
    No sample payload is read and no external metadata tool is trusted.
    """
    tops = list(_qt_box_spans(data))
    moovs = [row for row in tops if row[0] == b"moov"]
    if len(moovs) != 1:
        raise LivePhotoIntegrityError("MOV must contain exactly one moov box")
    _typ, _start, moov_payload, moov_end = moovs[0]
    mvhd = _qt_one_child(data, moov_payload, moov_end, b"mvhd")
    movie_timescale = _qt_mvhd_timescale(data, mvhd[2], mvhd[3])

    matches: list[dict] = []
    track_index = -1
    for typ, _track_start, trak_payload, trak_end in _qt_box_spans(data, moov_payload, moov_end):
        if typ != b"trak":
            continue
        track_index += 1
        mdia = _qt_one_child(data, trak_payload, trak_end, b"mdia")
        hdlr = _qt_one_child(data, mdia[2], mdia[3], b"hdlr")
        if _qt_handler_type(data, hdlr[2], hdlr[3]) != b"meta":
            continue
        mdhd = _qt_one_child(data, mdia[2], mdia[3], b"mdhd")
        track_timescale = _qt_mdhd_timescale(data, mdhd[2], mdhd[3])
        minf = _qt_one_child(data, mdia[2], mdia[3], b"minf")
        stbl = _qt_one_child(data, minf[2], minf[3], b"stbl")
        stsd = _qt_one_child(data, stbl[2], stbl[3], b"stsd")
        if not _qt_stsd_declares_still_image_time(data, stsd[2], stsd[3]):
            continue
        stts = _qt_one_child(data, stbl[2], stbl[3], b"stts")
        sample_count = _qt_stts_sample_count(data, stts[2], stts[3])
        if sample_count != 1:
            raise LivePhotoIntegrityError("still-image-time metadata track must contain exactly one sample")
        empty_duration = 0
        edts = _qt_one_child(data, trak_payload, trak_end, b"edts", required=False)
        if edts is not None:
            elst = _qt_one_child(data, edts[2], edts[3], b"elst", required=False)
            if elst is not None:
                empty_duration = _qt_elst_leading_empty_duration(data, elst[2], elst[3])
        # Edit-list segment durations use the movie timescale, not the metadata
        # track timescale.  Integer microseconds are deterministic archival evidence.
        still_us = empty_duration * 1_000_000 // movie_timescale
        matches.append({
            "parser": LIVE_PHOTO_TIMING_PARSER,
            "track_index": track_index,
            "movie_timescale": movie_timescale,
            "track_timescale": track_timescale,
            "leading_empty_edit_duration": empty_duration,
            "sample_count": sample_count,
            "still_image_time_us": still_us,
            "still_image_time_seconds": still_us / 1_000_000.0,
        })
    if len(matches) != 1:
        if not matches:
            raise LivePhotoIntegrityError("MOV has no unambiguous still-image-time timed metadata track")
        raise LivePhotoIntegrityError("MOV has multiple still-image-time timed metadata tracks")
    return matches[0]

def _ffprobe_movie(path: Path, timeout_seconds: float) -> tuple[dict, dict]:
    exe = shutil.which("ffprobe")
    if not exe:
        raise LivePhotoIntegrityError("ffprobe is not installed")
    resolved = Path(exe).resolve()
    try:
        cp = subprocess.run(
            [str(resolved), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise LivePhotoIntegrityError(f"ffprobe timed out after {timeout_seconds:g}s") from e
    if cp.returncode:
        raise LivePhotoIntegrityError((cp.stderr or cp.stdout or "ffprobe failed").strip()[-2000:])
    try:
        obj = json.loads(cp.stdout or "{}")
    except Exception as e:
        raise LivePhotoIntegrityError(f"invalid ffprobe JSON: {e}") from e
    ident = {
        "path": str(resolved),
        "sha256": sha256_file(resolved),
    }
    return obj, ident


def _movie_content_identifier(probe: dict) -> str:
    tags: dict[str, object] = {}
    for section in [probe.get("format") or {}, *(probe.get("streams") or [])]:
        for k, v in (section.get("tags") or {}).items():
            tags[str(k).casefold()] = v
    value = tags.get("com.apple.quicktime.content.identifier") or tags.get("content_identifier")
    if not isinstance(value, str) or not value.strip():
        raise LivePhotoIntegrityError("MOV has no QuickTime ContentIdentifier")
    return value.strip()


def inspect_live_photo_pair(still_path: str | Path, movie_path: str | Path, *, timeout_seconds: float = 30.0) -> LivePhotoPairIntegrity:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    still = Path(still_path)
    movie = Path(movie_path)
    report = LivePhotoPairIntegrity(still_path=str(still), movie_path=str(movie))
    if still.suffix.casefold() not in {".jpg", ".jpeg", ".heic", ".heif"}:
        report.errors.append("v2 pair-integrity parser supports JPEG/HEIC/HEIF Live Photo stills only")
        return report
    if movie.suffix.casefold() != ".mov":
        report.errors.append("v1 pair-integrity parser supports MOV Live Photo movies only")
        return report
    if not still.is_file() or not movie.is_file():
        report.errors.append("both Live Photo components must be existing regular files")
        return report

    try:
        before_still = sha256_file(still)
        before_movie = sha256_file(movie)
        report.still_sha256 = before_still
        report.movie_sha256 = before_movie

        still_bytes = still.read_bytes()
        if still.suffix.casefold() in {".jpg", ".jpeg"}:
            still_cid = apple_content_identifier_from_jpeg_bytes(still_bytes)
            report.observations["still_identifier_parser"] = "jpeg-exif-apple-makernote-v1"
        else:
            still_cid = apple_content_identifier_from_heif_bytes(still_bytes)
            report.observations["still_identifier_parser"] = "heif-primary-exif-cdsc-apple-makernote-v1"
        probe, ffprobe_identity = _ffprobe_movie(movie, timeout_seconds)
        movie_cid = _movie_content_identifier(probe)
        report.still_content_identifier = still_cid
        report.movie_content_identifier = movie_cid
        report.observations["ffprobe"] = ffprobe_identity
        report.observations["video_streams"] = sum(1 for s in (probe.get("streams") or []) if s.get("codec_type") == "video")

        try:
            timing = apple_live_photo_still_image_time_from_mov_bytes(movie.read_bytes())
            report.observations["timed_metadata"] = timing
            report.still_image_time_us = int(timing["still_image_time_us"])
            report.still_image_time_seconds = float(timing["still_image_time_seconds"])
            report.checks["still_image_time_track_present"] = True
            report.checks["still_image_time_single_sample"] = timing.get("sample_count") == 1
        except Exception as e:
            report.checks["still_image_time_track_present"] = False
            report.checks["still_image_time_single_sample"] = False
            report.observations["timed_metadata_error"] = str(e)

        try:
            still_norm = _normalize_identifier(still_cid)
            movie_norm = _normalize_identifier(movie_cid)
            report.normalized_content_identifier = still_norm if still_norm == movie_norm else None
            report.checks["still_content_identifier_is_uuid"] = True
            report.checks["movie_content_identifier_is_uuid"] = True
            report.checks["content_identifiers_match"] = still_norm == movie_norm
        except Exception as e:
            report.checks["still_content_identifier_is_uuid"] = False
            report.checks["movie_content_identifier_is_uuid"] = False
            report.checks["content_identifiers_match"] = False
            report.errors.append(str(e))

        sv = validate_media(still, expected_sha256=before_still)
        mv = validate_media(movie, expected_sha256=before_movie)
        report.still_validation = sv.to_dict()
        report.movie_validation = mv.to_dict()
        report.checks["still_media_valid"] = sv.status == "passed" and sv.bytes_unchanged is True
        report.checks["movie_media_valid"] = mv.status == "passed" and mv.bytes_unchanged is True
        report.checks["still_validator_fingerprinted"] = isinstance(sv.validator_sha256, str) and len(sv.validator_sha256) == 64
        report.checks["movie_validator_fingerprinted"] = isinstance(mv.validator_sha256, str) and len(mv.validator_sha256) == 64
        report.checks["movie_has_video_stream"] = (mv.video_streams or 0) >= 1
        duration = None
        try:
            duration = float((probe.get("format") or {}).get("duration"))
        except (TypeError, ValueError):
            pass
        if duration is None or duration <= 0:
            durations = []
            for stream in probe.get("streams") or []:
                if stream.get("codec_type") != "video":
                    continue
                try:
                    durations.append(float(stream.get("duration")))
                except (TypeError, ValueError):
                    continue
            duration = max(durations) if durations else None
        report.observations["movie_duration_seconds"] = duration
        report.checks["still_image_time_within_movie"] = (
            report.still_image_time_seconds is not None
            and duration is not None and duration > 0
            and 0.0 <= report.still_image_time_seconds <= duration + 1e-6
        )

        after_still = sha256_file(still)
        after_movie = sha256_file(movie)
        report.checks["still_bytes_unchanged"] = after_still == before_still
        report.checks["movie_bytes_unchanged"] = after_movie == before_movie

        report.identity_verified = (
            report.checks.get("still_content_identifier_is_uuid") is True
            and report.checks.get("movie_content_identifier_is_uuid") is True
            and report.checks.get("content_identifiers_match") is True
        )
        report.component_health_verified = all(report.checks.get(k) is True for k in (
            "still_media_valid", "movie_media_valid", "still_validator_fingerprinted",
            "movie_validator_fingerprinted", "movie_has_video_stream", "still_bytes_unchanged", "movie_bytes_unchanged",
        ))
        report.timing_resource_verified = all(report.checks.get(k) is True for k in (
            "still_image_time_track_present", "still_image_time_single_sample", "still_image_time_within_movie",
        ))
        # v2 can independently prove the pair identifier, component health, and the
        # still-image-time resource.  It still does not claim Apple Photos import/
        # playback compatibility without a genuine Apple-platform acceptance lane.
        report.photos_usability_verified = False
        report.observations["photos_usability_note"] = (
            "Content-identifier pairing, component health and (when present) still-image-time semantics are independently assessed; genuine Apple Photos acceptance/playback is a separate qualification gate."
        )
    except Exception as e:
        report.errors.append(str(e))
    return report
