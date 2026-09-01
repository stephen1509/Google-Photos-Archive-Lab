from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from .model import Confidence, GoogleSidecar

class LocationPolicy(str,Enum):
    PREFER_GOOGLE_CURRENT='prefer_google_current'
    PREFER_ORIGINAL='prefer_original'
    REQUIRE_AGREEMENT='require_agreement'

@dataclass
class LocationFact:
    confidence:Confidence
    lat:float|None=None
    lon:float|None=None
    source:str='unknown'
    current_candidates:list[tuple[float,float]]=field(default_factory=list)
    original_candidates:list[tuple[float,float]]=field(default_factory=list)
    embedded_candidates:list[tuple[float,float]]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)

    @property
    def exact(self):return self.lat is not None and self.lon is not None and self.confidence in {Confidence.PROVEN,Confidence.USER_CONFIRMED}

def _unique(vals):
    out=[]
    for x in vals:
        if x is not None and x not in out:out.append(x)
    return out

def resolve_location(sidecars:list[GoogleSidecar],policy:LocationPolicy=LocationPolicy.PREFER_GOOGLE_CURRENT,embedded_gps:tuple[float,float]|None=None)->LocationFact:
    current=_unique([x.geo_current for x in sidecars]);original=_unique([x.geo_exif for x in sidecars]);embedded=_unique([embedded_gps]);warnings=[]
    if len(current)>1:warnings.append('conflicting Google-current GPS across duplicate evidence')
    if len(original)>1:warnings.append('conflicting Google-original/EXIF GPS across duplicate evidence')
    c=current[0] if len(current)==1 else None;o=original[0] if len(original)==1 else None;e=embedded[0] if len(embedded)==1 else None
    vals=[x for x in (c,o,e) if x is not None]
    if len(set(vals))>1:warnings.append('location evidence differs across Google-current/original and embedded camera GPS')
    chosen=None;source='unknown';confidence=Confidence.UNKNOWN
    if policy==LocationPolicy.PREFER_GOOGLE_CURRENT:
        if c:chosen=c;source='google-current';confidence=Confidence.PROVEN
        elif e:chosen=e;source='embedded-camera';confidence=Confidence.PROVEN
        elif o:chosen=o;source='google-original-exif';confidence=Confidence.PROVEN
    elif policy==LocationPolicy.PREFER_ORIGINAL:
        if e:chosen=e;source='embedded-camera';confidence=Confidence.PROVEN
        elif o:chosen=o;source='google-original-exif';confidence=Confidence.PROVEN
        elif c:chosen=c;source='google-current';confidence=Confidence.PROVEN
    else:
        unique=set(vals)
        if len(unique)==1 and vals:
            chosen=vals[0];source='location-evidence-agrees' if len(vals)>1 else ('embedded-camera-only' if e else ('google-current-only' if c else 'google-original-only'));confidence=Confidence.PROVEN
        elif len(unique)>1:
            confidence=Confidence.CONFLICT;source='location-conflict';warnings.append('strict location policy refused to choose conflicting GPS')
    if chosen:return LocationFact(confidence,chosen[0],chosen[1],source,current,original,embedded,warnings)
    if confidence!=Confidence.CONFLICT and (len(current)>1 or len(original)>1):confidence=Confidence.CONFLICT;source='location-conflict'
    return LocationFact(confidence,None,None,source,current,original,embedded,warnings)
