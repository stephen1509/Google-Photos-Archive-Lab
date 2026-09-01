from __future__ import annotations
import hashlib, struct
from pathlib import Path

class MediaStructureError(Exception): pass

def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()

def jpeg_payload_fingerprint(data:bytes)->str:
    if not data.startswith(b'\xff\xd8'): raise MediaStructureError('not jpeg')
    h=hashlib.sha256();h.update(data[:2]);i=2
    metadata_markers={0xE1,0xED,0xFE}  # APP1 EXIF/XMP, APP13 IPTC, COM
    while i < len(data):
        if data[i] != 0xFF: raise MediaStructureError('bad jpeg marker')
        while i < len(data) and data[i]==0xFF:i+=1
        if i>=len(data):break
        marker=data[i];i+=1
        if marker==0xD9: h.update(b'\xff\xd9');break
        if marker==0xDA: # Start of scan: hash header + all entropy-coded data through EOI
            if i+2>len(data):raise MediaStructureError('truncated sos')
            L=int.from_bytes(data[i:i+2],'big')
            start=i-2; scan_header_end=i+L
            if scan_header_end>len(data):raise MediaStructureError('truncated sos header')
            h.update(data[start:])
            return h.hexdigest()
        if marker in {0xD0,0xD1,0xD2,0xD3,0xD4,0xD5,0xD6,0xD7,0x01}: # standalone
            h.update(bytes([0xFF,marker]));continue
        if i+2>len(data):raise MediaStructureError('truncated segment')
        L=int.from_bytes(data[i:i+2],'big')
        if L<2 or i+L>len(data):raise MediaStructureError('bad segment length')
        seg=data[i-2:i+L]
        if marker not in metadata_markers:h.update(seg)
        i+=L
    return h.hexdigest()

def _png_chunks(data:bytes):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise MediaStructureError('not png')
    i=8
    while i+12<=len(data):
        L=int.from_bytes(data[i:i+4],'big');typ=data[i+4:i+8];end=i+12+L
        if end>len(data):raise MediaStructureError('truncated png')
        yield typ,data[i:end]
        i=end
        if typ==b'IEND':return
    raise MediaStructureError('missing IEND')

def png_visual_fingerprint(data:bytes)->str:
    h=hashlib.sha256();h.update(data[:8])
    metadata={b'eXIf',b'tEXt',b'zTXt'}
    for typ,chunk in _png_chunks(data):
        if typ==b'iTXt':
            # Skip only Adobe XMP iTXt; other text is retained conservatively.
            payload=chunk[8:-4]
            if payload.startswith(b'XML:com.adobe.xmp\x00'):continue
        if typ in metadata:continue
        h.update(chunk)
    return h.hexdigest()

def _boxes(data:bytes,start=0,end=None):
    end=len(data) if end is None else end;i=start
    while i+8<=end:
        size=int.from_bytes(data[i:i+4],'big');typ=data[i+4:i+8];header=8
        if size==1:
            if i+16>end:raise MediaStructureError('truncated largesize')
            size=int.from_bytes(data[i+8:i+16],'big');header=16
        elif size==0:size=end-i
        if size<header or i+size>end:raise MediaStructureError('bad box')
        yield typ,i+header,i+size
        i+=size
    if i!=end:raise MediaStructureError('trailing bytes')

def mp4_mdat_fingerprint(data:bytes)->str:
    h=hashlib.sha256();count=0
    for typ,s,e in _boxes(data):
        if typ==b'mdat':h.update(data[s:e]);count+=1
    if not count:raise MediaStructureError('no mdat')
    return h.hexdigest()


# HEIF/AVIF item payload fingerprinting --------------------------------------
#
# This intentionally fingerprints item payload bytes rather than the whole mdat.
# EXIF and XMP are separate HEIF metadata items and may legitimately change during
# a metadata-only write.  Every other item is protected conservatively, including
# thumbnails, alpha/depth auxiliaries and derived-image item data.

_HEIF_XMP_MIME = b'application/rdf+xml'


def _box_spans(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else end
    i = start
    while i + 8 <= end:
        size = int.from_bytes(data[i:i+4], 'big')
        typ = data[i+4:i+8]
        header = 8
        if size == 1:
            if i + 16 > end:
                raise MediaStructureError('truncated largesize')
            size = int.from_bytes(data[i+8:i+16], 'big')
            header = 16
        elif size == 0:
            size = end - i
        if size < header or i + size > end:
            raise MediaStructureError('bad box')
        yield typ, i, i + header, i + size
        i += size
    if i != end:
        raise MediaStructureError('trailing bytes')


def _fullbox(data: bytes, payload_start: int, end: int):
    if payload_start + 4 > end:
        raise MediaStructureError('truncated fullbox')
    return data[payload_start], int.from_bytes(data[payload_start+1:payload_start+4], 'big'), payload_start + 4


def _uintn(data: bytes, pos: int, size: int, end: int, label: str):
    if size < 0 or size > 8:
        raise MediaStructureError(f'unsupported {label} width')
    if size == 0:
        return 0, pos
    if pos + size > end:
        raise MediaStructureError(f'truncated {label}')
    return int.from_bytes(data[pos:pos+size], 'big'), pos + size


def _cstring(data: bytes, pos: int, end: int, label: str):
    try:
        stop = data.index(0, pos, end)
    except ValueError as e:
        raise MediaStructureError(f'unterminated {label}') from e
    return data[pos:stop], stop + 1


def _parse_infe(data: bytes, payload_start: int, end: int):
    version, _flags, pos = _fullbox(data, payload_start, end)
    if version == 2:
        item_id, pos = _uintn(data, pos, 2, end, 'infe item id')
    elif version == 3:
        item_id, pos = _uintn(data, pos, 4, end, 'infe item id')
    else:
        raise MediaStructureError('unsupported infe version')
    _protection, pos = _uintn(data, pos, 2, end, 'infe protection index')
    if pos + 4 > end:
        raise MediaStructureError('truncated infe item type')
    item_type = data[pos:pos+4]
    pos += 4
    item_name, pos = _cstring(data, pos, end, 'infe item name')
    content_type = None
    if item_type == b'mime':
        content_type, pos = _cstring(data, pos, end, 'infe MIME content type')
        # content_encoding is optional and irrelevant to XMP recognition.
        if pos < end:
            _encoding, pos = _cstring(data, pos, end, 'infe MIME content encoding')
    return item_id, item_type, item_name, content_type


def _parse_iinf(data: bytes, payload_start: int, end: int):
    version, _flags, pos = _fullbox(data, payload_start, end)
    if version == 0:
        count, pos = _uintn(data, pos, 2, end, 'iinf entry count')
    elif version == 1:
        count, pos = _uintn(data, pos, 4, end, 'iinf entry count')
    else:
        raise MediaStructureError('unsupported iinf version')
    items = {}
    seen = 0
    for typ, _box_start, child_payload, child_end in _box_spans(data, pos, end):
        if typ != b'infe':
            raise MediaStructureError('unexpected iinf child')
        item_id, item_type, item_name, content_type = _parse_infe(data, child_payload, child_end)
        if item_id in items:
            raise MediaStructureError('duplicate item id in iinf')
        items[item_id] = (item_type, item_name, content_type)
        seen += 1
    if seen != count:
        raise MediaStructureError('iinf entry count mismatch')
    return items


def _parse_iloc(data: bytes, payload_start: int, end: int, *, mdat_payloads, idat_payload):
    version, _flags, pos = _fullbox(data, payload_start, end)
    if version not in {0, 1, 2}:
        raise MediaStructureError('unsupported iloc version')
    if pos + 2 > end:
        raise MediaStructureError('truncated iloc field sizes')
    first, second = data[pos], data[pos+1]
    pos += 2
    offset_size, length_size = first >> 4, first & 0x0F
    base_offset_size = second >> 4
    index_size = (second & 0x0F) if version in {1, 2} else 0
    for label, width in (
        ('iloc offset', offset_size), ('iloc length', length_size),
        ('iloc base offset', base_offset_size), ('iloc extent index', index_size),
    ):
        if width > 8:
            raise MediaStructureError(f'unsupported {label} width')
    item_count, pos = _uintn(data, pos, 4 if version == 2 else 2, end, 'iloc item count')
    locations = {}
    for _ in range(item_count):
        item_id, pos = _uintn(data, pos, 4 if version == 2 else 2, end, 'iloc item id')
        if item_id in locations:
            raise MediaStructureError('duplicate item id in iloc')
        construction_method = 0
        if version in {1, 2}:
            raw_method, pos = _uintn(data, pos, 2, end, 'iloc construction method')
            if raw_method & 0xFFF0:
                raise MediaStructureError('reserved iloc construction bits are nonzero')
            construction_method = raw_method & 0x000F
        data_reference_index, pos = _uintn(data, pos, 2, end, 'iloc data reference index')
        if data_reference_index != 0:
            raise MediaStructureError('external HEIF data reference is unsupported')
        base_offset, pos = _uintn(data, pos, base_offset_size, end, 'iloc base offset')
        extent_count, pos = _uintn(data, pos, 2, end, 'iloc extent count')
        if extent_count < 1:
            raise MediaStructureError('HEIF item has no extents')
        if construction_method not in {0, 1}:
            raise MediaStructureError('unsupported HEIF item construction method')
        ranges = []
        for _extent in range(extent_count):
            if version in {1, 2} and index_size:
                _extent_index, pos = _uintn(data, pos, index_size, end, 'iloc extent index')
            extent_offset, pos = _uintn(data, pos, offset_size, end, 'iloc extent offset')
            extent_length, pos = _uintn(data, pos, length_size, end, 'iloc extent length')
            if extent_length <= 0:
                raise MediaStructureError('zero-length HEIF extent is unsupported')
            if construction_method == 0:
                start = base_offset + extent_offset
                stop = start + extent_length
                if start < 0 or stop < start or not any(start >= a and stop <= b for a, b in mdat_payloads):
                    raise MediaStructureError('HEIF extent is outside mdat payload')
            else:
                if idat_payload is None:
                    raise MediaStructureError('idat construction used without idat')
                idat_start, idat_end = idat_payload
                start = idat_start + base_offset + extent_offset
                stop = start + extent_length
                if start < idat_start or stop < start or stop > idat_end:
                    raise MediaStructureError('HEIF extent is outside idat payload')
            ranges.append((start, stop))
        locations[item_id] = tuple(ranges)
    if pos != end:
        raise MediaStructureError('trailing iloc bytes')
    return locations


def _is_permitted_heif_metadata_item(item_type: bytes, content_type: bytes | None) -> bool:
    if item_type == b'Exif':
        return True
    return item_type == b'mime' and content_type == _HEIF_XMP_MIME


def heif_item_payload_fingerprint(data: bytes) -> str:
    """Fingerprint all HEIF/AVIF non-EXIF/non-XMP item payloads.

    The result is independent of item IDs and metadata-item placement, but preserves
    item type, extent order and exact item payload bytes.  Only file-local mdat
    (construction method 0) and idat (method 1) storage are admitted.  Unsupported
    or ambiguous layouts fail closed.
    """
    top = list(_box_spans(data))
    metas = [row for row in top if row[0] == b'meta']
    if len(metas) != 1:
        raise MediaStructureError('expected exactly one file-level HEIF meta box')
    mdat_payloads = [(payload, end) for typ, _start, payload, end in top if typ == b'mdat']
    if not mdat_payloads:
        # idat-only files are valid, so this alone is not an error.
        mdat_payloads = []
    _typ, _meta_start, meta_payload, meta_end = metas[0]
    _meta_version, _meta_flags, child_start = _fullbox(data, meta_payload, meta_end)
    iinf_boxes = []
    iloc_boxes = []
    idat_boxes = []
    for typ, _start, payload, end in _box_spans(data, child_start, meta_end):
        if typ == b'iinf':
            iinf_boxes.append((payload, end))
        elif typ == b'iloc':
            iloc_boxes.append((payload, end))
        elif typ == b'idat':
            idat_boxes.append((payload, end))
    if len(iinf_boxes) != 1 or len(iloc_boxes) != 1:
        raise MediaStructureError('expected exactly one iinf and one iloc box')
    if len(idat_boxes) > 1:
        raise MediaStructureError('multiple idat boxes are unsupported')
    items = _parse_iinf(data, *iinf_boxes[0])
    idat_payload = idat_boxes[0] if idat_boxes else None
    locations = _parse_iloc(data, *iloc_boxes[0], mdat_payloads=mdat_payloads, idat_payload=idat_payload)
    if set(locations) - set(items):
        raise MediaStructureError('iloc contains unknown item id')

    protected = []
    protected_ranges = []
    metadata_ranges = []
    for item_id, (item_type, _item_name, content_type) in items.items():
        ranges = locations.get(item_id)
        is_metadata = _is_permitted_heif_metadata_item(item_type, content_type)
        if ranges is None:
            if is_metadata:
                continue
            raise MediaStructureError('non-metadata HEIF item has no location')
        if is_metadata:
            metadata_ranges.extend(ranges)
            continue
        payload = b''.join(data[a:b] for a, b in ranges)
        if not payload:
            raise MediaStructureError('empty HEIF item payload')
        protected_ranges.extend(ranges)
        protected.append((item_type, len(payload), hashlib.sha256(payload).digest()))

    if not protected:
        raise MediaStructureError('HEIF file contains no protected item payloads')
    for a, b in protected_ranges:
        for c, d in metadata_ranges:
            if max(a, c) < min(b, d):
                raise MediaStructureError('metadata and protected HEIF extents overlap')

    protected.sort(key=lambda row: (row[0], row[1], row[2]))
    h = hashlib.sha256()
    h.update(b'gpa-heif-item-payload-v1\0')
    h.update(len(protected).to_bytes(4, 'big'))
    for item_type, length, digest in protected:
        h.update(item_type)
        h.update(length.to_bytes(8, 'big'))
        h.update(digest)
    return h.hexdigest()
