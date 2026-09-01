from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PurePosixPath
import re

from .model import Confidence, DatePrecision

_SEQ_RE=re.compile(r'^(?P<prefix>.*?)(?P<num>\d+)(?P<tail>[^\d]*)$')

@dataclass(frozen=True)
class ContextNeighbor:
    asset_id:str
    filename:str
    sequence_number:int
    capture_time:str
    camera:str|None=None

@dataclass(frozen=True)
class DateReviewSuggestion:
    asset_id:str
    confidence:Confidence
    start:datetime
    end:datetime
    before:ContextNeighbor
    after:ContextNeighbor
    reasons:tuple[str,...]=()
    note:str='Context suggestion only; it must never become archival truth without stronger evidence or user confirmation.'


def _sequence_signature(path:str):
    p=PurePosixPath(path);m=_SEQ_RE.match(p.stem)
    if not m:return None
    return str(p.parent),m.group('prefix').casefold(),m.group('tail').casefold(),p.suffix.casefold(),int(m.group('num'))


def _exact_dt(asset):
    f=getattr(asset,'date_fact',None)
    if not f or f.precision!=DatePrecision.INSTANT or f.confidence not in {Confidence.PROVEN,Confidence.USER_CONFIRMED} or f.value is None:return None
    return f.value


def _camera(asset)->str|None:
    e=getattr(asset,'embedded',None)
    if not e:return None
    parts=[x.strip() for x in (getattr(e,'make',None),getattr(e,'model',None)) if isinstance(x,str) and x.strip()]
    return ' '.join(parts) or None


def suggest_sequence_date_range(assets,asset_id:str,*,max_sequence_gap:int=100,max_span_days:int=120)->DateReviewSuggestion|None:
    """Suggest a bounded date range from neighboring *filename sequence* evidence.

    This is deliberately a review aid, not a chronology resolver.  It only uses a
    target whose date is not already exact, requires exact dated neighbors on both
    sides in the same logical directory/name sequence, and never mutates the asset.
    """
    by_id={a.asset_id:a for a in assets};target=by_id.get(asset_id)
    if target is None or _exact_dt(target) is not None or not getattr(target,'occurrences',None):return None
    tpath=target.occurrences[0].path;sig=_sequence_signature(tpath)
    if sig is None:return None
    tdir,tprefix,ttail,text,tnum=sig;tcamera=_camera(target)
    lower=[];upper=[]
    for a in assets:
        if a.asset_id==asset_id or not getattr(a,'occurrences',None):continue
        s=_sequence_signature(a.occurrences[0].path)
        if s is None:continue
        d,prefix,tail,ext,num=s
        if (d,prefix,tail,ext)!=(tdir,tprefix,ttail,text):continue
        if abs(num-tnum)>max_sequence_gap:continue
        dt=_exact_dt(a)
        if dt is None:continue
        acamera=_camera(a)
        # If both target and neighbor identify a camera and disagree, don't use it.
        if tcamera and acamera and tcamera.casefold()!=acamera.casefold():continue
        row=(abs(num-tnum),num,a,dt,acamera)
        (lower if num<tnum else upper if num>tnum else []).append(row)
    if not lower or not upper:return None
    before=min(lower,key=lambda x:(x[0],-x[1]));after=min(upper,key=lambda x:(x[0],x[1]))
    bdt=before[3];adt=after[3]
    try:span=adt-bdt
    except TypeError:return None  # aware/naive mismatch is itself unresolved evidence
    if span<timedelta(0) or span>timedelta(days=max_span_days):return None
    bpath=PurePosixPath(before[2].occurrences[0].path).name;apath=PurePosixPath(after[2].occurrences[0].path).name
    reasons=[f'filename sequence is bounded by {bpath} and {apath} in the same logical folder']
    if tcamera and before[4] and after[4] and tcamera.casefold()==before[4].casefold()==after[4].casefold():
        reasons.append(f'all three assets identify the same camera: {tcamera}')
    return DateReviewSuggestion(
        asset_id,Confidence.PROBABLE,bdt,adt,
        ContextNeighbor(before[2].asset_id,bpath,before[1],bdt.isoformat(),before[4]),
        ContextNeighbor(after[2].asset_id,apath,after[1],adt.isoformat(),after[4]),
        tuple(reasons),
    )
