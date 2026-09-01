from __future__ import annotations
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path, PurePosixPath

from .model import Confidence, DatePrecision
from .review_context import suggest_sequence_date_range

@dataclass(frozen=True)
class ReviewItem:
    asset_id:str
    filename:str
    planned_path:str|None
    blocking:list[str]=field(default_factory=list)
    optional:list[str]=field(default_factory=list)
    sources:list[dict]=field(default_factory=list)
    date_evidence:dict|None=None
    location_evidence:dict|None=None
    camera:dict|None=None
    albums:list[dict]=field(default_factory=list)
    family_relations:list[dict]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)
    date_suggestion:dict|None=None
    decision_options:list[str]=field(default_factory=list)

    @property
    def blocks_placement(self)->bool:
        return bool(self.blocking)

    def to_dict(self)->dict:
        d=asdict(self);d['blocks_placement']=self.blocks_placement;return d


def _date_dict(f):
    if not f:return None
    return {
        'precision':f.precision.value,'confidence':f.confidence.value,
        'value':f.value.isoformat() if f.value else None,'year':f.year,'month':f.month,
        'start':f.start.isoformat() if f.start else None,'end':f.end.isoformat() if f.end else None,
        'timezone_known':f.timezone_known,'source':f.source,'note':f.note,
    }


def _location_dict(loc,place=None,user_label=None):
    if not loc and not place and not user_label:return None
    d={
        'confidence':getattr(getattr(loc,'confidence',None),'value',None),
        'lat':getattr(loc,'lat',None),'lon':getattr(loc,'lon',None),
        'source':getattr(loc,'source',None),'warnings':list(getattr(loc,'warnings',[]) or []),
        'user_label':user_label,
    }
    if place:
        d['derived_place']=asdict(place) if hasattr(place,'__dataclass_fields__') else dict(place)
    return d


def _date_blocks(asset)->list[str]:
    f=getattr(asset,'placement_fact',None) or getattr(asset,'date_fact',None)
    if not f:return ['capture date/placement is unknown']
    reasons=[]
    if f.confidence in {Confidence.UNKNOWN,Confidence.CONFLICT,Confidence.PROBABLE}:
        reasons.append(f'capture date confidence is {f.confidence.value}')
    if f.precision in {DatePrecision.UNKNOWN,DatePrecision.RANGE,DatePrecision.YEAR}:
        reasons.append(f'capture date precision is {f.precision.value}; a unique year/month folder is not known')
    out=getattr(asset,'output_dir',None) or ''
    if out.startswith('_Needs Placement') and not reasons:reasons.append('asset is still in Needs Placement')
    return reasons


def build_review_queue(assets)->list[ReviewItem]:
    assets=list(assets);out=[]
    for a in assets:
        blocking=_date_blocks(a);optional=[]
        loc=getattr(a,'location_fact',None);label=getattr(a,'user_location_label',None)
        if loc is None or not getattr(loc,'exact',False):
            if not label:optional.append('exact location/GPS is unknown')
        elif loc.confidence in {Confidence.PROBABLE,Confidence.CONFLICT}:
            optional.append(f'location confidence is {loc.confidence.value}')
        if getattr(a,'timezone_suggestion',None) and not getattr(a,'timezone_evidence',None):optional.append('timezone has only a suggestion')
        if not blocking and not optional and not getattr(a,'warnings',None):continue
        occ=list(getattr(a,'occurrences',[]) or []);filename=PurePosixPath(occ[0].path).name if occ else (getattr(a,'output_name',None) or a.asset_id)
        planned=(f"{a.output_dir}/{a.output_name}" if getattr(a,'output_dir',None) and getattr(a,'output_name',None) else None)
        suggestion=suggest_sequence_date_range(assets,a.asset_id) if blocking else None
        camera=None;e=getattr(a,'embedded',None)
        if e and (getattr(e,'make',None) or getattr(e,'model',None)):
            camera={'make':getattr(e,'make',None),'model':getattr(e,'model',None)}
        options=[]
        if blocking:options.extend(['confirm_month','confirm_exact_date_or_time','confirm_date_range','leave_unresolved'])
        if optional:options.extend(['confirm_location_label','confirm_exact_gps','leave_location_unknown'])
        # De-duplicate while preserving order.
        options=list(dict.fromkeys(options))
        out.append(ReviewItem(
            a.asset_id,filename,planned,blocking,optional,
            [{'archive':r.archive,'path':r.path} for r in occ],
            _date_dict(getattr(a,'date_fact',None)),
            _location_dict(loc,getattr(a,'place_evidence',None),label),camera,
            [{'album_id':x.album_id,'title':x.title,'logical_dir':x.logical_dir} for x in getattr(a,'albums',[]) or []],
            list(getattr(a,'family_relations',[]) or []),list(getattr(a,'warnings',[]) or []),
            ({'confidence':suggestion.confidence.value,'start':suggestion.start.isoformat(),'end':suggestion.end.isoformat(),'before':asdict(suggestion.before),'after':asdict(suggestion.after),'reasons':list(suggestion.reasons),'note':suggestion.note} if suggestion else None),
            options,
        ))
    return sorted(out,key=lambda x:(not x.blocks_placement,x.planned_path or '',x.asset_id))


def write_review_queue(path:Path,items:list[ReviewItem])->Path:
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    payload={'schema':'gpa.review-queue.v1','items':[x.to_dict() for x in items]}
    data=(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(path);return path
