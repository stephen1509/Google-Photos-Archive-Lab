from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
from zoneinfo import ZoneInfo
from .model import Confidence, DateFact, GoogleSidecar
from .chronology import resolve_google_time

class TimezoneLookupUnavailable(Exception):pass

@dataclass(frozen=True)
class BoundaryDataset:
    name:str
    release:str
    temporal_mode:str='current'  # 'since1970', 'current', or 'comprehensive'
    sha256:str|None=None

    @property
    def identity(self)->str:
        base=f'{self.name}:{self.release}:{self.temporal_mode}'
        return f'{base}:sha256-{self.sha256[:16]}' if self.sha256 else base

@dataclass(frozen=True)
class TimezoneEvidence:
    zone:str
    confidence:Confidence
    dataset:str
    note:str|None=None
    tzdata_version:str|None=None




def boundary_dataset_from_data_pack(pack_root:Path,manifest_path:Path)->BoundaryDataset:
    """Create boundary provenance only after the offline pack verifies exactly."""
    from .datapacks import load_data_pack_manifest,verify_data_pack
    manifest=load_data_pack_manifest(manifest_path);check=verify_data_pack(pack_root,manifest,report_extras=False)
    if not check.ok:raise ValueError('timezone-boundary data-pack verification failed')
    if manifest.role!='timezone-boundaries':raise ValueError(f'unexpected data-pack role: {manifest.role}')
    mode=manifest.temporal_mode or 'current'
    digest=hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
    return BoundaryDataset(manifest.name,manifest.version,mode,digest)

def system_tzdata_version()->str|None:
    """Best-effort IANA tzdata version without network access."""
    for p in (Path('/usr/share/zoneinfo/tzdata.zi'),Path('/usr/share/lib/zoneinfo/tzdata.zi')):
        try:
            first=p.open('r',encoding='utf-8',errors='replace').readline().strip()
        except OSError:continue
        if first.startswith('# version '):return first.split(None,2)[2]
    try:
        import importlib.metadata as md
        return 'python-tzdata-'+md.version('tzdata')
    except Exception:return None


def confidence_for_boundary(dataset:BoundaryDataset,capture_year:int|None)->tuple[Confidence,str]:
    if dataset.temporal_mode=='since1970':
        if capture_year is not None and capture_year>=1970:
            return Confidence.PROVEN,'timezone boundary product is designed for timekeeping equivalence since 1970'
        return Confidence.PROBABLE,'since-1970 boundary product cannot prove pre-1970 location boundaries/timekeeping'
    if dataset.temporal_mode=='current':
        return Confidence.PROBABLE,'current/now boundary product is not authoritative for historical observations'
    # Comprehensive geometry is useful spatial evidence, but its exact historical applicability is not declared by this model.
    return Confidence.PROBABLE,'comprehensive boundary lookup; historical boundary applicability not proven'


class OfflineTimezoneResolver:
    def __init__(self,dataset:BoundaryDataset|None=None):
        self.dataset=dataset or BoundaryDataset('timezonefinder','installed','current')
        try:
            from timezonefinder import TimezoneFinder
        except ImportError:
            self._tf=None
        else:self._tf=TimezoneFinder(in_memory=True)
    @property
    def available(self):return self._tf is not None
    def zone_at(self,lat:float,lon:float)->str:
        if not self._tf:raise TimezoneLookupUnavailable('timezonefinder dataset not installed')
        z=self._tf.timezone_at(lat=lat,lng=lon)
        if not z:raise TimezoneLookupUnavailable('no timezone polygon match')
        return z
    def evidence_at(self,lat:float,lon:float,capture_year:int|None=None)->TimezoneEvidence:
        zone=self.zone_at(lat,lon);conf,note=confidence_for_boundary(self.dataset,capture_year)
        return TimezoneEvidence(zone,conf,self.dataset.identity,note,system_tzdata_version())
    def local_from_epoch(self,epoch:int,zone:str)->datetime:
        return datetime.fromtimestamp(epoch,timezone.utc).astimezone(ZoneInfo(zone))


def resolve_with_timezone_evidence(sidecar:GoogleSidecar,evidence:TimezoneEvidence)->tuple[DateFact,datetime|None]:
    suggested=None
    if sidecar.taken_epoch is not None:
        suggested=datetime.fromtimestamp(sidecar.taken_epoch,timezone.utc).astimezone(ZoneInfo(evidence.zone))
    if evidence.confidence==Confidence.PROVEN:
        f=resolve_google_time(sidecar,evidence.zone)
        f.source=f'google+timezone:{evidence.dataset}:{evidence.zone}'
        tzv=f'; tzdata={evidence.tzdata_version}' if evidence.tzdata_version else ''
        f.note=(evidence.note or '')+tzv
        return f,suggested
    f=resolve_google_time(sidecar,None)
    extra=f' Probable local time using {evidence.zone} from {evidence.dataset}: {suggested.isoformat()}' if suggested else ''
    f.note=(f.note or '')+extra
    return f,suggested
