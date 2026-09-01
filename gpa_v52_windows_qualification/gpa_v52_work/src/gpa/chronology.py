from __future__ import annotations
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from .model import DateFact, DatePrecision, Confidence, GoogleSidecar

MIN_OFFSET=-12
MAX_OFFSET=14

def month_candidates_for_utc_epoch(epoch:int)->set[tuple[int,int]]:
    dt=datetime.fromtimestamp(epoch,timezone.utc)
    out=set()
    # include half-hours / quarter-hours indirectly by checking each 15 min offset.
    for q in range(MIN_OFFSET*4,MAX_OFFSET*4+1):
        local=dt+timedelta(minutes=q*15)
        out.add((local.year,local.month))
    return out

def resolve_google_time(sidecar:GoogleSidecar, timezone_name:str|None=None)->DateFact:
    if sidecar.taken_epoch is None:
        return DateFact(DatePrecision.UNKNOWN,Confidence.UNKNOWN,source='google')
    utc_dt=datetime.fromtimestamp(sidecar.taken_epoch,timezone.utc)
    if timezone_name:
        local=utc_dt.astimezone(ZoneInfo(timezone_name))
        return DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=local,year=local.year,month=local.month,timezone_known=True,source='google+timezone')
    months=month_candidates_for_utc_epoch(sidecar.taken_epoch)
    if len(months)==1:
        y,m=next(iter(months))
        return DateFact(DatePrecision.MONTH,Confidence.PROVEN,year=y,month=m,timezone_known=False,source='google-utc-month-invariant',note='Exact local clock unknown; month invariant across plausible global offsets')
    return DateFact(DatePrecision.RANGE,Confidence.PROBABLE,start=utc_dt+timedelta(hours=MIN_OFFSET),end=utc_dt+timedelta(hours=MAX_OFFSET),timezone_known=False,source='google-utc-ambiguous',note='Unknown timezone crosses a month/year boundary')

def derive_offset_from_camera_local(epoch:int, camera_local:datetime)->timedelta|None:
    if camera_local.tzinfo is not None:
        return camera_local.utcoffset()
    utc_naive=datetime.fromtimestamp(epoch,timezone.utc).replace(tzinfo=None)
    delta=camera_local-utc_naive
    # Normalize by whole days to a plausible timezone offset, but reject if seconds not 15-minute aligned.
    while delta < timedelta(hours=MIN_OFFSET): delta += timedelta(days=1)
    while delta > timedelta(hours=MAX_OFFSET): delta -= timedelta(days=1)
    mins=delta.total_seconds()/60
    if mins % 15 != 0:return None
    if not timedelta(hours=MIN_OFFSET)<=delta<=timedelta(hours=MAX_OFFSET):return None
    return delta
