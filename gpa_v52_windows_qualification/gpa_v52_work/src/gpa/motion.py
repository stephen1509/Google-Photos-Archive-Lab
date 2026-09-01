from __future__ import annotations
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET
from .fingerprint import _boxes, _box_spans, _fullbox, _parse_iinf, _parse_iloc, MediaStructureError

@dataclass(frozen=True)
class MotionVideo:
    start:int
    end:int
    container:str

@dataclass
class MotionItem:
    semantic:str|None
    mime:str|None
    length:int|None
    padding:int|None

@dataclass
class MotionDescriptor:
    motion_flag:int|None=None
    version:int|None=None
    presentation_timestamp_us:int|None=None
    items:list[MotionItem]=field(default_factory=list)
    legacy_microvideo_offset:int|None=None
    warnings:list[str]=field(default_factory=list)

    @property
    def motion_item(self)->MotionItem|None:
        rows=[x for x in self.items if x.semantic=='MotionPhoto']
        return rows[0] if len(rows)==1 else None
    @property
    def primary_item(self)->MotionItem|None:
        rows=[x for x in self.items if x.semantic=='Primary']
        return rows[0] if len(rows)==1 else None
    @property
    def structurally_declared(self)->bool:
        m=self.motion_item;p=self.primary_item
        return self.motion_flag==1 and m is not None and p is not None and bool(m.length and m.length>0)

def _local(tag:str)->str:return tag.rsplit('}',1)[-1].split(':')[-1]
def _int(v):
    try:return int(v)
    except (TypeError,ValueError):return None

def parse_motion_xmp(xmp:bytes|str)->MotionDescriptor:
    """Parse Google Motion Photo XMP without trusting namespace prefixes or filenames."""
    d=MotionDescriptor()
    try:root=ET.fromstring(xmp)
    except ET.ParseError:
        d.warnings.append('invalid XMP XML');return d
    for e in root.iter():
        attrs={_local(k):v for k,v in e.attrib.items()}
        if 'MotionPhoto' in attrs:d.motion_flag=_int(attrs.get('MotionPhoto'))
        if 'MotionPhotoVersion' in attrs:d.version=_int(attrs.get('MotionPhotoVersion'))
        if 'MotionPhotoPresentationTimestampUs' in attrs:d.presentation_timestamp_us=_int(attrs.get('MotionPhotoPresentationTimestampUs'))
        if 'MicroVideoOffset' in attrs:d.legacy_microvideo_offset=_int(attrs.get('MicroVideoOffset'))
        if 'Semantic' in attrs or 'Mime' in attrs:
            d.items.append(MotionItem(attrs.get('Semantic'),attrs.get('Mime'),_int(attrs.get('Length')),_int(attrs.get('Padding'))))
    if d.motion_flag not in (None,0,1):d.warnings.append('undefined MotionPhoto flag treated as non-motion')
    if d.motion_flag==1 and not d.motion_item:d.warnings.append('MotionPhoto flag present but no unique MotionPhoto container item')
    if len([x for x in d.items if x.semantic=='Primary'])!=1 and d.items:d.warnings.append('container must have exactly one Primary item')
    if len([x for x in d.items if x.semantic=='MotionPhoto'])>1:d.warnings.append('container has multiple MotionPhoto items')
    return d

def _looks_isobmff_video(b:bytes)->bool:
    try:boxes=list(_boxes(b))
    except MediaStructureError:return False
    types={t for t,_,_ in boxes}
    return b'ftyp' in types and (b'moov' in types or b'mdat' in types)

def locate_jpeg_motion_video(data:bytes,video_length:int|None)->MotionVideo|None:
    if not data.startswith(b'\xff\xd8') or not video_length or video_length<=0 or video_length>len(data):return None
    start=len(data)-video_length;video=data[start:]
    if not _looks_isobmff_video(video):return None
    return MotionVideo(start,len(data),'appended-isobmff')

def locate_jpeg_from_descriptor(data:bytes,descriptor:MotionDescriptor,allow_legacy_microvideo:bool=False)->MotionVideo|None:
    if descriptor.motion_flag!=1:return None
    length=descriptor.motion_item.length if descriptor.motion_item else None
    if not length and allow_legacy_microvideo:length=descriptor.legacy_microvideo_offset
    return locate_jpeg_motion_video(data,length)

def locate_heif_motion_video(data:bytes)->MotionVideo|None:
    try:boxes=list(_boxes(data))
    except MediaStructureError:return None
    if not boxes or boxes[-1][0]!=b'mpvd':return None
    _,s,e=boxes[-1];payload=data[s:e]
    if not payload or not _looks_isobmff_video(payload):return None
    return MotionVideo(s,e,'mpvd')

def validate_heif_descriptor(data:bytes,descriptor:MotionDescriptor)->MotionVideo|None:
    if descriptor.motion_flag!=1:return None
    p=descriptor.primary_item;m=descriptor.motion_item
    if not p or not m or p.padding!=8:return None
    found=locate_heif_motion_video(data)
    if not found:return None
    if m.length is not None and m.length!=(found.end-found.start):return None
    return found



def heif_xmp_packets(data:bytes)->list[bytes]:
    """Extract file-level HEIF/AVIF XMP metadata items without trusting ExifTool.

    Only a single file-level ``meta`` box with one ``iinf``/``iloc`` mapping is
    accepted. Unsupported external references or construction methods fail closed
    through the shared HEIF extent parser.
    """
    try:
        top=list(_box_spans(data))
        metas=[row for row in top if row[0]==b'meta']
        if len(metas)!=1:return []
        _typ,_start,meta_payload,meta_end=metas[0]
        _version,_flags,child_start=_fullbox(data,meta_payload,meta_end)
        children=list(_box_spans(data,child_start,meta_end))
        iinf=[row for row in children if row[0]==b'iinf']
        iloc=[row for row in children if row[0]==b'iloc']
        idat=[row for row in children if row[0]==b'idat']
        if len(iinf)!=1 or len(iloc)!=1 or len(idat)>1:return []
        items=_parse_iinf(data,iinf[0][2],iinf[0][3])
        mdat_payloads=[(payload,end) for typ,_s,payload,end in top if typ==b'mdat']
        idat_payload=(idat[0][2],idat[0][3]) if idat else None
        locations=_parse_iloc(data,iloc[0][2],iloc[0][3],mdat_payloads=mdat_payloads,idat_payload=idat_payload)
        out=[]
        for item_id,(item_type,_name,content_type) in items.items():
            if item_type!=b'mime' or content_type!=b'application/rdf+xml':continue
            ranges=locations.get(item_id)
            if not ranges:continue
            out.append(b''.join(data[a:b] for a,b in ranges).rstrip(b'\x00'))
        return out
    except (MediaStructureError,ValueError,IndexError):
        return []


def descriptor_from_heif(data:bytes)->MotionDescriptor|None:
    candidates=[]
    for raw in heif_xmp_packets(data):
        d=parse_motion_xmp(raw)
        if d.motion_flag is not None or d.items or d.legacy_microvideo_offset is not None:candidates.append(d)
    if not candidates:return None
    structural=[d for d in candidates if d.structurally_declared]
    if len(structural)==1:return structural[0]
    if len(structural)>1:
        d=structural[0];d.warnings.append('multiple Motion Photo XMP items found; automatic selection is ambiguous');return d
    return candidates[0]


def detect_heif_motion_photo(data:bytes)->tuple[MotionDescriptor,MotionVideo]|None:
    d=descriptor_from_heif(data)
    if not d:return None
    v=validate_heif_descriptor(data,d)
    if not v:return None
    return d,v

def live_photo_pair(still_content_id:str|None,movie_content_id:str|None)->bool:
    return bool(still_content_id and movie_content_id and still_content_id==movie_content_id)

_XMP_APP1_HEADER=b'http://ns.adobe.com/xap/1.0/\x00'

def jpeg_xmp_packets(data:bytes)->list[bytes]:
    """Extract standard XMP APP1 packets without entering JPEG entropy-coded data."""
    if not data.startswith(b'\xff\xd8'):return []
    out=[];pos=2;n=len(data)
    while pos+2<=n:
        if data[pos]!=0xff:return out
        while pos<n and data[pos]==0xff:pos+=1
        if pos>=n:return out
        marker=data[pos];pos+=1
        if marker in {0xd9,0xda}:break  # EOI or SOS; metadata segments are before scan data
        if marker==0x01 or 0xd0<=marker<=0xd7:continue
        if pos+2>n:return out
        seglen=int.from_bytes(data[pos:pos+2],'big')
        if seglen<2 or pos+seglen>n:return out
        payload=data[pos+2:pos+seglen]
        if marker==0xe1 and payload.startswith(_XMP_APP1_HEADER):out.append(payload[len(_XMP_APP1_HEADER):])
        pos+=seglen
    return out


def descriptor_from_jpeg(data:bytes)->MotionDescriptor|None:
    candidates=[]
    for raw in jpeg_xmp_packets(data):
        d=parse_motion_xmp(raw)
        if d.motion_flag is not None or d.items or d.legacy_microvideo_offset is not None:candidates.append(d)
    if not candidates:return None
    # Prefer a structurally declared packet. Multiple competing structural packets are ambiguous.
    structural=[d for d in candidates if d.structurally_declared]
    if len(structural)==1:return structural[0]
    if len(structural)>1:
        d=structural[0];d.warnings.append('multiple Motion Photo XMP packets found; automatic selection is ambiguous');return d
    return candidates[0]


def detect_jpeg_motion_photo(data:bytes,allow_legacy_microvideo:bool=False)->tuple[MotionDescriptor,MotionVideo]|None:
    d=descriptor_from_jpeg(data)
    if not d:return None
    v=locate_jpeg_from_descriptor(data,d,allow_legacy_microvideo=allow_legacy_microvideo)
    if not v:return None
    return d,v
