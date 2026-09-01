from __future__ import annotations
from dataclasses import dataclass, field
from .model import DateFact, DatePrecision, GoogleSidecar


def _xmp_gps_coordinate(value:float,is_latitude:bool)->str:
    """EXIF-for-XMP GPSCoordinate text: degrees,fractional-minutes + hemisphere."""
    v=float(value);av=abs(v);deg=int(av);minutes=(av-deg)*60.0
    hemi=('N' if v>=0 else 'S') if is_latitude else ('E' if v>=0 else 'W')
    mins=(f'{minutes:.8f}').rstrip('0').rstrip('.')
    if not mins:mins='0'
    return f'{deg},{mins}{hemi}'

@dataclass
class MetadataPlan:
    exif:dict[str,str]=field(default_factory=dict)
    xmp:dict[str,str]=field(default_factory=dict)
    archival:dict=field(default_factory=dict)
    warnings:list[str]=field(default_factory=list)

def plan_metadata(date:DateFact|None,sidecar:GoogleSidecar|None,location_label:str|None=None,exact_gps:tuple[float,float]|None=None)->MetadataPlan:
    p=MetadataPlan()
    if date:
        p.archival['date']={'precision':date.precision.value,'confidence':date.confidence.value,'source':date.source,'note':date.note}
        if date.precision==DatePrecision.INSTANT and date.value:
            v=date.value
            # EXIF complete local clock; offset separately if known.
            p.exif['DateTimeOriginal']=v.strftime('%Y:%m:%d %H:%M:%S')
            p.xmp['XMP-photoshop:DateCreated']=v.isoformat()
            if date.timezone_known and v.utcoffset() is not None:
                total=int(v.utcoffset().total_seconds()//60);sign='+' if total>=0 else '-';total=abs(total)
                p.exif['OffsetTimeOriginal']=f'{sign}{total//60:02d}:{total%60:02d}'
        elif date.precision==DatePrecision.MONTH and date.year and date.month:
            p.xmp['XMP-photoshop:DateCreated']=f'{date.year:04d}-{date.month:02d}'
        elif date.precision==DatePrecision.YEAR and date.year:
            p.xmp['XMP-photoshop:DateCreated']=f'{date.year:04d}'
        elif date.precision==DatePrecision.RANGE:
            p.archival['date']['start']=date.start.isoformat() if date.start else None
            p.archival['date']['end']=date.end.isoformat() if date.end else None
            p.warnings.append('date range retained in archival record; not written as false exact EXIF date')
    if exact_gps:
        lat,lon=exact_gps;p.archival['gps']={'lat':lat,'lon':lon,'precision':'exact'}
        p.exif['GPSLatitude']=str(abs(lat));p.exif['GPSLatitudeRef']='N' if lat>=0 else 'S'
        p.exif['GPSLongitude']=str(abs(lon));p.exif['GPSLongitudeRef']='E' if lon>=0 else 'W'
        p.xmp['XMP-exif:GPSLatitude']=_xmp_gps_coordinate(lat,True)
        p.xmp['XMP-exif:GPSLongitude']=_xmp_gps_coordinate(lon,False)
    if location_label:
        p.archival['location_label']=location_label
        p.warnings.append('unstructured place label retained in archival record; structured city/state/country not guessed')
    if sidecar:
        p.archival['google']={'favorited':sidecar.favorited,'people':sidecar.people,'description':sidecar.description,'raw':sidecar.raw}
        if sidecar.description:p.xmp['dc:description']=sidecar.description
    return p
