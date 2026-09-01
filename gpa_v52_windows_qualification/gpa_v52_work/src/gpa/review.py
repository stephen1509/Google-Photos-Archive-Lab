from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from .model import Confidence, DateFact, DatePrecision

@dataclass(frozen=True)
class Decision:
    asset_id:str
    field:str
    value:dict
    actor:str='user'
    rationale:str|None=None
    timestamp:str=''

    def __post_init__(self):
        if not self.timestamp:
            object.__setattr__(self,'timestamp',datetime.now(timezone.utc).isoformat())

class DecisionJournal:
    def __init__(self,path:str|Path):self.path=Path(path)
    def append(self,d:Decision):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a',encoding='utf-8') as f:
            f.write(json.dumps(asdict(d),ensure_ascii=False,sort_keys=True)+'\n')
    def read(self)->list[Decision]:
        if not self.path.exists():return []
        out=[]
        for line in self.path.read_text(encoding='utf-8').splitlines():
            if line.strip():out.append(Decision(**json.loads(line)))
        return out

def user_month(year:int,month:int)->DateFact:
    if not 1<=month<=12:raise ValueError('month')
    return DateFact(DatePrecision.MONTH,Confidence.USER_CONFIRMED,year=year,month=month,source='user',note='User confirmed month; day/time remain unknown')

def user_year(year:int)->DateFact:
    return DateFact(DatePrecision.YEAR,Confidence.USER_CONFIRMED,year=year,source='user')


def user_range(start:datetime,end:datetime)->DateFact:
    if end<start:raise ValueError('range end before start')
    return DateFact(DatePrecision.RANGE,Confidence.USER_CONFIRMED,start=start,end=end,source='user',note='User confirmed range; exact date/time remain unknown')

def user_month_range(start_year:int,start_month:int,end_year:int,end_month:int)->DateFact:
    import calendar
    if not (1<=start_month<=12 and 1<=end_month<=12):raise ValueError('month')
    start=datetime(start_year,start_month,1)
    end=datetime(end_year,end_month,calendar.monthrange(end_year,end_month)[1],23,59,59,999999)
    return user_range(start,end)


def date_decision(asset_id:str,fact:DateFact,*,rationale:str|None=None)->Decision:
    """Create a durable user chronology decision without increasing its precision."""
    value={
        'precision':fact.precision.value,'confidence':Confidence.USER_CONFIRMED.value,
        'year':fact.year,'month':fact.month,
        'value':fact.value.isoformat() if fact.value else None,
        'start':fact.start.isoformat() if fact.start else None,
        'end':fact.end.isoformat() if fact.end else None,
        'timezone_known':fact.timezone_known,'note':fact.note,
    }
    return Decision(asset_id,'date',value,rationale=rationale)


def location_label_decision(asset_id:str,label:str,*,rationale:str|None=None)->Decision:
    label=str(label).strip()
    if not label:raise ValueError('location label')
    return Decision(asset_id,'location_label',{'label':label},rationale=rationale)


def location_gps_decision(asset_id:str,lat:float,lon:float,*,rationale:str|None=None)->Decision:
    lat=float(lat);lon=float(lon)
    if not (-90<=lat<=90 and -180<=lon<=180) or (lat==0 and lon==0):raise ValueError('GPS')
    return Decision(asset_id,'location_gps',{'lat':lat,'lon':lon},rationale=rationale)


def _date_from_decision(d:Decision)->DateFact:
    v=d.value or {};precision=DatePrecision(v.get('precision','unknown'))
    def dt(k):
        x=v.get(k);return datetime.fromisoformat(x) if x else None
    return DateFact(
        precision,Confidence.USER_CONFIRMED,value=dt('value'),year=v.get('year'),month=v.get('month'),
        start=dt('start'),end=dt('end'),timezone_known=bool(v.get('timezone_known',False)),
        source='user',note=v.get('note') or 'User-confirmed chronology; original evidence remains preserved separately.'
    )


def latest_decisions(decisions:list[Decision],asset_id:str)->dict[str,Decision]:
    """Latest decision per field drives the projection; the journal remains append-only."""
    out={}
    for d in decisions:
        if d.asset_id==asset_id:out[d.field]=d
    return out


def apply_user_decisions(asset,decisions:list[Decision],*,place_resolver=None)->bool:
    """Apply explicit user decisions to one planned asset and retain complete provenance.

    Returns True if an effective field was changed.  The function never derives an
    exact day/time from a month/year/range decision and never derives coordinates
    from a textual place label.
    """
    from dataclasses import asdict
    from .location import LocationFact
    from .output import placement_dir
    current=latest_decisions(decisions,asset.asset_id);changed=False
    if not hasattr(asset,'user_decisions'):asset.user_decisions=[]
    asset.user_decisions=[asdict(d) for d in decisions if d.asset_id==asset.asset_id]
    d=current.get('date')
    if d:
        f=_date_from_decision(d);asset.date_fact=f;asset.placement_fact=f;asset.output_dir=placement_dir(f);changed=True
    d=current.get('location_gps')
    if d:
        lat=float(d.value['lat']);lon=float(d.value['lon']);old=getattr(asset,'location_fact',None)
        asset.location_fact=LocationFact(
            Confidence.USER_CONFIRMED,lat,lon,'user-confirmed-gps',
            list(getattr(old,'current_candidates',[]) or []),list(getattr(old,'original_candidates',[]) or []),
            list(getattr(old,'embedded_candidates',[]) or []),list(getattr(old,'warnings',[]) or []),
        )
        changed=True
        if place_resolver:
            try:
                asset.place_evidence=place_resolver.nearest(lat,lon) if hasattr(place_resolver,'nearest') else place_resolver(lat,lon)
            except Exception as e:
                asset.warnings.append(f'place enrichment failed after user GPS decision: {e}')
    d=current.get('location_label')
    if d:
        asset.user_location_label=str(d.value['label']).strip();changed=True
    return changed


def apply_decision_journal(assets,journal:DecisionJournal|str|Path,*,place_resolver=None)->int:
    if not isinstance(journal,DecisionJournal):journal=DecisionJournal(journal)
    decisions=journal.read();changed=0
    for a in assets:
        if apply_user_decisions(a,decisions,place_resolver=place_resolver):changed+=1
    if changed:
        from .output import resolve_filename_collisions
        names=resolve_filename_collisions([(a.asset_id,a.output_dir or '',a.output_name or '_') for a in assets])
        for a in assets:a.output_name=names[a.asset_id]
    return changed
