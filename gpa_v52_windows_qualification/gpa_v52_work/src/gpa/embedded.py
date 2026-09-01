from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from .chronology import derive_offset_from_camera_local
from .model import Confidence, DateFact, DatePrecision, GoogleSidecar
from .formats import PILLOW_STILL_SUFFIXES, VIDEO_SUFFIXES

_DT_RE = re.compile(r'^(?P<y>\d{4}):(?P<m>\d{2}):(?P<d>\d{2})[ T](?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})(?:\.(?P<sub>\d+))?$')
_OFF_RE = re.compile(r'^(?P<sign>[+-])(?P<h>\d{2}):(?P<m>\d{2})$')

@dataclass
class EmbeddedMetadata:
    capture_local: datetime | None = None
    offset: timedelta | None = None
    gps: tuple[float, float] | None = None
    make: str | None = None
    model: str | None = None
    content_identifier: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def capture_aware(self) -> datetime | None:
        if self.capture_local is None:
            return None
        if self.capture_local.tzinfo is not None:
            return self.capture_local
        if self.offset is None:
            return None
        return self.capture_local.replace(tzinfo=timezone(self.offset))


def _first(tags:dict[str,Any], *names:str):
    for n in names:
        if n in tags and tags[n] not in (None, ''):
            return tags[n]
    return None


def parse_exif_datetime(value:Any)->datetime|None:
    if not isinstance(value,str):return None
    m=_DT_RE.match(value.strip())
    if not m:return None
    g=m.groupdict();micro=0
    if g['sub']:
        micro=int((g['sub']+'000000')[:6])
    try:return datetime(int(g['y']),int(g['m']),int(g['d']),int(g['H']),int(g['M']),int(g['S']),micro)
    except ValueError:return None


def parse_offset(value:Any)->timedelta|None:
    if not isinstance(value,str):return None
    m=_OFF_RE.match(value.strip())
    if not m:return None
    h=int(m.group('h'));minute=int(m.group('m'))
    if h>14 or minute>59:return None
    delta=timedelta(hours=h,minutes=minute)
    if m.group('sign')=='-':delta=-delta
    if delta < timedelta(hours=-12) or delta > timedelta(hours=14):return None
    return delta


def _float(v:Any)->float|None:
    try:return float(v)
    except (TypeError,ValueError):return None


def _valid_gps(lat:float|None,lon:float|None)->tuple[float,float]|None:
    if lat is None or lon is None:return None
    if not (-90<=lat<=90 and -180<=lon<=180):return None
    if lat==0 and lon==0:return None
    return (lat,lon)


def embedded_from_exiftool(tags:dict[str,Any], *, kind:str='still')->EmbeddedMetadata:
    """Normalize a conservative subset of ExifTool output into archive evidence.

    This function deliberately does not interpret filesystem dates as capture dates.
    For still images, EXIF DateTimeOriginal is preferred. For QuickTime-based media,
    capture time is only accepted from explicitly capture-oriented keys.
    """
    e=EmbeddedMetadata(raw=dict(tags))
    dt=_first(tags,'EXIF:DateTimeOriginal','ExifIFD:DateTimeOriginal','XMP-exif:DateTimeOriginal','XMP-photoshop:DateCreated')
    parsed=parse_exif_datetime(dt)
    if parsed is None and isinstance(dt,str) and dt:
        # ISO-style XMP DateCreated may include an offset.
        try:
            parsed=datetime.fromisoformat(dt.replace('Z','+00:00'))
        except ValueError:
            e.warnings.append('unparseable embedded capture datetime')
    e.capture_local=parsed
    off=parse_offset(_first(tags,'EXIF:OffsetTimeOriginal','ExifIFD:OffsetTimeOriginal'))
    if parsed is not None and parsed.tzinfo is not None:
        e.offset=parsed.utcoffset()
    else:e.offset=off
    lat=_float(_first(tags,'EXIF:GPSLatitude','GPS:GPSLatitude','XMP-exif:GPSLatitude','Composite:GPSLatitude'))
    lon=_float(_first(tags,'EXIF:GPSLongitude','GPS:GPSLongitude','XMP-exif:GPSLongitude','Composite:GPSLongitude'))
    e.gps=_valid_gps(lat,lon)
    if (lat is not None or lon is not None) and e.gps is None:e.warnings.append('embedded GPS invalid or placeholder')
    make=_first(tags,'EXIF:Make','IFD0:Make','QuickTime:Make','Keys:Make')
    model=_first(tags,'EXIF:Model','IFD0:Model','QuickTime:Model','Keys:Model')
    e.make=str(make).strip() if make not in (None,'') else None
    e.model=str(model).strip() if model not in (None,'') else None
    if kind=='still':
        cid=_first(tags,'Apple:ContentIdentifier','MakerNotes:ContentIdentifier')
    else:
        cid=_first(tags,'Keys:ContentIdentifier','QuickTime:ContentIdentifier')
    e.content_identifier=str(cid).strip() if cid not in (None,'') else None
    return e


def camera_date_fact(e:EmbeddedMetadata|None)->DateFact|None:
    if not e or not e.capture_local:return None
    dt=e.capture_local
    if dt.tzinfo is not None:
        return DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=dt,year=dt.year,month=dt.month,timezone_known=True,source='embedded-camera')
    if e.offset is not None:
        aware=dt.replace(tzinfo=timezone(e.offset))
        return DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=aware,year=aware.year,month=aware.month,timezone_known=True,source='embedded-camera+offset')
    # The camera-local calendar value is still usable for YYYY/MM placement, but the
    # instant and timezone are unknown. Do not invent an offset.
    return DateFact(DatePrecision.MONTH,Confidence.PROVEN,year=dt.year,month=dt.month,timezone_known=False,source='embedded-camera-local-month',note='Original camera local date exists but timezone/instant is unknown')


def reconcile_google_and_camera(sidecars:list[GoogleSidecar], embedded:EmbeddedMetadata|None)->DateFact:
    """Choose a conservative effective chronology while preserving conflict semantics.

    - If Google UTC and camera-local time mathematically establish a plausible offset,
      return an exact local instant with proven cross-source agreement.
    - If both establish months and disagree, return a conflict.
    - Otherwise prefer a Google exact/placement fact when available, falling back to
      embedded camera evidence. This does not erase either raw evidence.
    """
    from .chronology import resolve_google_time
    gfacts=[resolve_google_time(s) for s in sidecars if s.taken_epoch is not None]
    cf=camera_date_fact(embedded)
    epochs={s.taken_epoch for s in sidecars if s.taken_epoch is not None}
    if embedded and embedded.capture_local and embedded.capture_local.tzinfo is None and len(epochs)==1:
        epoch=next(iter(epochs));off=embedded.offset or derive_offset_from_camera_local(epoch,embedded.capture_local)
        if off is not None:
            aware=embedded.capture_local.replace(tzinfo=timezone(off))
            # Require actual instant agreement; offset derivation alone should not upgrade a bad camera clock.
            if abs(aware.timestamp()-epoch)<=1:
                return DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=aware,year=aware.year,month=aware.month,timezone_known=True,source='google+embedded-camera-agree',note=f'UTC offset {off} established by Google instant and embedded camera local time')
    if embedded and embedded.capture_aware and len(epochs)==1:
        aware=embedded.capture_aware;epoch=next(iter(epochs))
        if abs(aware.timestamp()-epoch)<=1:
            return DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=aware,year=aware.year,month=aware.month,timezone_known=True,source='google+embedded-camera-agree')
    months=set()
    for f in gfacts:
        if f.year and f.month and f.precision!=DatePrecision.RANGE:months.add((f.year,f.month))
    if cf and cf.year and cf.month:months.add((cf.year,cf.month))
    if len(months)>1:
        return DateFact(DatePrecision.UNKNOWN,Confidence.CONFLICT,source='google-camera-conflict',note=f'Google and embedded camera chronology disagree on month: {sorted(months)}')
    exact=[f for f in gfacts if f.precision==DatePrecision.INSTANT and f.confidence==Confidence.PROVEN]
    if exact:return exact[0]
    if gfacts:
        # Google may provide only a month-invariant placement or a boundary range.
        g=gfacts[0]
        if g.precision!=DatePrecision.RANGE:return g
    if cf:return cf
    if gfacts:return gfacts[0]
    return DateFact(DatePrecision.UNKNOWN,Confidence.UNKNOWN,source='no-chronology-evidence')


def _ratio_float(v:Any)->float:
    try:return float(v)
    except (TypeError,ValueError,ZeroDivisionError):
        if isinstance(v,(tuple,list)) and len(v)==2 and float(v[1])!=0:return float(v[0])/float(v[1])
        raise


def _dms_to_decimal(v:Any,ref:Any)->float|None:
    try:
        if not isinstance(v,(tuple,list)) or len(v)!=3:return None
        d,m,s=(_ratio_float(x) for x in v);out=d+m/60+s/3600
    except Exception:return None
    r=str(ref or '').upper()
    if r in {'S','W'}:out=-out
    return out


def embedded_from_pillow_bytes(data:bytes)->EmbeddedMetadata|None:
    """Read common still-image EXIF without decoding pixels.

    This is intentionally a conservative fallback for formats Pillow understands;
    HEIC/AVIF/RAW/QuickTime remain on the ExifTool/format-specific lane.
    """
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as im:
            ex=im.getexif()
            if not ex:return EmbeddedMetadata(raw={})
            raw={f'EXIF:{k}':v for k,v in ex.items() if k!=34853}
            tags={
                'EXIF:DateTimeOriginal':ex.get(36867),
                'EXIF:OffsetTimeOriginal':ex.get(36881),
                'EXIF:Make':ex.get(271),
                'EXIF:Model':ex.get(272),
            }
            try:gps=ex.get_ifd(34853)
            except Exception:gps={}
            if gps:
                lat=_dms_to_decimal(gps.get(2),gps.get(1));lon=_dms_to_decimal(gps.get(4),gps.get(3))
                if lat is not None:tags['EXIF:GPSLatitude']=lat
                if lon is not None:tags['EXIF:GPSLongitude']=lon
                raw['EXIF:GPSInfo']=dict(gps)
            e=embedded_from_exiftool(tags,kind='still');e.raw={'pillow_exif':raw};return e
    except Exception:
        return None


class PillowZipEmbeddedProvider:
    """Planner provider for common still-image metadata directly from ZIP members.

    ``batch`` deliberately opens each source ZIP once and inspects members one at a
    time.  This keeps memory bounded while avoiding the catastrophic cost of
    reopening a large Takeout ZIP for every photograph.
    """
    suffixes=PILLOW_STILL_SUFFIXES

    def __call__(self,member,lib)->EmbeddedMetadata|None:
        if member.suffix not in self.suffixes:return None
        try:data=lib.read(member)
        except Exception:return None
        return embedded_from_pillow_bytes(data)

    def batch(self,members,lib)->dict[tuple[str,str],EmbeddedMetadata|None]:
        import os, zipfile
        grouped={}
        for m in members:
            if m.suffix in self.suffixes:grouped.setdefault(m.archive,[]).append(m)
        result={}
        for archive,group in grouped.items():
            try:before=os.stat(archive)
            except OSError:
                for m in group:result[(m.archive,m.path)]=None
                continue
            try:
                with zipfile.ZipFile(archive) as z:
                    for m in group:
                        try:
                            with z.open(m.open_name) as f:data=f.read()
                            result[(m.archive,m.path)]=embedded_from_pillow_bytes(data)
                        except Exception:
                            result[(m.archive,m.path)]=None
            except Exception:
                for m in group:result.setdefault((m.archive,m.path),None)
            finally:
                try:after=os.stat(archive)
                except OSError:continue
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    # A changed source archive invalidates evidence gathered from it.
                    for m in group:result[(m.archive,m.path)]=None
        return result

_ISO6709_RE=re.compile(r'^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?/?$')

def _flatten_ffprobe_tags(probe:dict)->dict[str,Any]:
    out={}
    for section in [probe.get('format') or {},*(probe.get('streams') or [])]:
        for k,v in (section.get('tags') or {}).items():
            out[str(k).casefold()]=v
    return out


def _stable_ffprobe_evidence(probe:dict)->dict:
    """Return archival ffprobe evidence with ephemeral staging paths removed.

    ffprobe's ``format.filename`` echoes the local path passed to the subprocess.
    Takeout movies are probed from disposable staging directories, so preserving
    that value would make identical media produce a different immutable revision
    on every import.  The source archive/member path is already preserved elsewhere
    in the GPA record and is the correct provenance for archival identity.
    """
    import copy
    stable=copy.deepcopy(probe)
    fmt=stable.get('format')
    if isinstance(fmt,dict):fmt.pop('filename',None)
    return stable


def embedded_from_ffprobe(probe:dict)->EmbeddedMetadata:
    """Normalize only QuickTime fields whose semantics are sufficiently clear.

    Generic creation_time is intentionally retained in raw evidence but is not
    promoted to capture time here: container creation semantics vary by producer.
    Ephemeral probe/staging paths are excluded from archived raw evidence.
    """
    tags=_flatten_ffprobe_tags(probe);e=EmbeddedMetadata(raw={'ffprobe':_stable_ffprobe_evidence(probe)})
    cid=tags.get('com.apple.quicktime.content.identifier') or tags.get('content_identifier')
    if cid:e.content_identifier=str(cid).strip()
    loc=tags.get('com.apple.quicktime.location.iso6709') or tags.get('location')
    if isinstance(loc,str):
        m=_ISO6709_RE.match(loc.strip())
        if m:
            e.gps=_valid_gps(float(m.group(1)),float(m.group(2)))
            if e.gps is None:e.warnings.append('QuickTime ISO6709 location invalid or placeholder')
    make=tags.get('com.apple.quicktime.make') or tags.get('make');model=tags.get('com.apple.quicktime.model') or tags.get('model')
    e.make=str(make).strip() if make else None;e.model=str(model).strip() if model else None
    if 'creation_time' in tags:e.warnings.append('QuickTime creation_time retained as raw evidence only; not assumed to be capture time')
    return e


class FFprobeZipEmbeddedProvider:
    suffixes=VIDEO_SUFFIXES
    def __init__(self,ffprobe='ffprobe'):self.ffprobe=ffprobe
    def __call__(self,member,lib)->EmbeddedMetadata|None:
        if member.suffix not in self.suffixes:return None
        from pathlib import Path
        import tempfile
        from .video import probe_video
        with tempfile.TemporaryDirectory(prefix='gpa-probe-') as td:
            p,_=lib.extract_to_staging(member,Path(td))
            try:return embedded_from_ffprobe(probe_video(p,self.ffprobe))
            finally:p.unlink(missing_ok=True)

    def batch(self,members,lib)->dict[tuple[str,str],EmbeddedMetadata|None]:
        """Probe movies while opening each source ZIP only once.

        ffprobe still runs once per movie because it validates the actual container,
        but archive decompression is streamed through one ZipFile handle per archive.
        This removes a large avoidable cost for video-heavy Takeouts without reading
        whole movies into memory.
        """
        import os, shutil, tempfile, zipfile, hashlib
        from pathlib import Path
        from .video import probe_video
        grouped={}
        for m in members:
            if m.suffix in self.suffixes:grouped.setdefault(m.archive,[]).append(m)
        result={}
        with tempfile.TemporaryDirectory(prefix='gpa-probe-batch-') as td:
            troot=Path(td)
            for archive,group in grouped.items():
                try:before=os.stat(archive)
                except OSError:
                    for m in group:result[(m.archive,m.path)]=None
                    continue
                try:
                    with zipfile.ZipFile(archive) as z:
                        for idx,m in enumerate(group):
                            suffix=m.suffix if m.suffix else '.bin'
                            token=hashlib.sha256((m.path+'\0'+str(idx)).encode('utf-8')).hexdigest()[:16]
                            p=troot/f'{token}{suffix}'
                            try:
                                with z.open(m.open_name) as src,p.open('wb') as dst:shutil.copyfileobj(src,dst,length=1024*1024)
                                result[(m.archive,m.path)]=embedded_from_ffprobe(probe_video(p,self.ffprobe))
                            except Exception:
                                result[(m.archive,m.path)]=None
                            finally:p.unlink(missing_ok=True)
                except Exception:
                    for m in group:result.setdefault((m.archive,m.path),None)
                finally:
                    try:after=os.stat(archive)
                    except OSError:continue
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                        for m in group:result[(m.archive,m.path)]=None
        return result


class CompositeZipEmbeddedProvider:
    def __init__(self,still=None,movie=None):
        self.still=still or PillowZipEmbeddedProvider();self.movie=movie or FFprobeZipEmbeddedProvider()
    def __call__(self,member,lib)->EmbeddedMetadata|None:
        if member.suffix in FFprobeZipEmbeddedProvider.suffixes:return self.movie(member,lib)
        return self.still(member,lib)

    def batch(self,members,lib)->dict[tuple[str,str],EmbeddedMetadata|None]:
        """Batch the inexpensive still-image lane; keep movie probing explicit.

        ffprobe starts a subprocess per movie, so ZIP-open overhead is not the main
        cost there.  Stills, however, may number in the hundreds of thousands and
        must not reopen the archive once per asset.
        """
        members=list(members);result={}
        stills=[m for m in members if m.suffix not in FFprobeZipEmbeddedProvider.suffixes]
        movies=[m for m in members if m.suffix in FFprobeZipEmbeddedProvider.suffixes]
        if hasattr(self.still,'batch'):
            result.update(self.still.batch(stills,lib))
        else:
            for m in stills:
                try:result[(m.archive,m.path)]=self.still(m,lib)
                except Exception:result[(m.archive,m.path)]=None
        if hasattr(self.movie,'batch'):
            result.update(self.movie.batch(movies,lib))
        else:
            for m in movies:
                try:result[(m.archive,m.path)]=self.movie(m,lib)
                except Exception:result[(m.archive,m.path)]=None
        return result
