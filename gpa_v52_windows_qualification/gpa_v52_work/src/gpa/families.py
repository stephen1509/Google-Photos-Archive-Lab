from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from .model import Confidence, DateFact, DatePrecision
from .output import placement_dir
from .formats import LIVE_PHOTO_STILL_SUFFIXES as STILL_SUFFIXES, LIVE_PHOTO_MOVIE_SUFFIXES as MOVIE_SUFFIXES, RAW_SUFFIXES

@dataclass(frozen=True)
class EmbeddedEvidence:
    asset_id:str
    filename:str
    apple_content_id:str|None=None

    @property
    def kind(self)->str:
        s=PurePosixPath(self.filename).suffix.lower()
        if s in STILL_SUFFIXES:return 'still'
        if s in MOVIE_SUFFIXES:return 'movie'
        return 'other'

@dataclass
class FamilyRelation:
    kind:str
    member_ids:list[str]
    confidence:Confidence
    key:str|None=None
    warnings:list[str]=field(default_factory=list)


def content_identifier_from_exiftool(tags:dict,kind:str)->str|None:
    preferred=('Apple:ContentIdentifier',) if kind=='still' else ('Keys:ContentIdentifier','QuickTime:ContentIdentifier')
    for k in preferred:
        v=tags.get(k)
        if isinstance(v,str) and v.strip():return v.strip()
    # ExifTool group names can vary with extraction flags; accept only clearly grouped suffix matches.
    for k,v in tags.items():
        if not isinstance(v,str) or not v.strip():continue
        if k.endswith(':ContentIdentifier'):
            group=k.split(':',1)[0]
            if kind=='still' and group in {'Apple','MakerNotes'}:return v.strip()
            if kind=='movie' and group in {'Keys','QuickTime'}:return v.strip()
    return None


def match_live_photos(evidence:list[EmbeddedEvidence])->list[FamilyRelation]:
    by={}
    for e in evidence:
        if e.apple_content_id:by.setdefault(e.apple_content_id,[]).append(e)
    out=[]
    for cid,rows in by.items():
        still=[e for e in rows if e.kind=='still'];movie=[e for e in rows if e.kind=='movie']
        if len(still)==1 and len(movie)==1:
            out.append(FamilyRelation('apple-live-photo',[still[0].asset_id,movie[0].asset_id],Confidence.PROVEN,cid))
        elif still or movie:
            out.append(FamilyRelation('apple-live-photo',[e.asset_id for e in rows],Confidence.CONFLICT,cid,['content identifier is not a unique 1:1 still/movie pair']))
    return out


def _month_key(f:DateFact|None):
    if f and f.year and f.month and f.precision in {DatePrecision.INSTANT,DatePrecision.DAY,DatePrecision.MONTH}:return (f.year,f.month)
    return None


def apply_live_photo_placement(assets:dict[str,object],relation:FamilyRelation,*,record_relation:bool=True)->None:
    """Use a proven Live Photo relationship for *placement only*.

    The companion's own capture metadata is never fabricated.  When the month
    source is user-confirmed, inherited *folder placement* carries that same
    confidence instead of being mislabeled as independently proven.
    """
    if relation.kind!='apple-live-photo' or relation.confidence!=Confidence.PROVEN or len(relation.member_ids)!=2:return
    if any(x not in assets for x in relation.member_ids):return
    a,b=(assets[x] for x in relation.member_ids)
    ka=_month_key(a.date_fact);kb=_month_key(b.date_fact)
    if ka and kb and ka!=kb:
        conflict=DateFact(DatePrecision.UNKNOWN,Confidence.CONFLICT,source='live-photo-placement-conflict',note=f'Live Photo components disagree on month: {ka} vs {kb}')
        a.placement_fact=conflict;b.placement_fact=conflict
        if conflict.note not in a.warnings:a.warnings.append(conflict.note)
        if conflict.note not in b.warnings:b.warnings.append(conflict.note)
    elif ka and not kb:
        a.placement_fact=a.date_fact
        inherited_conf=a.date_fact.confidence if a.date_fact else Confidence.PROVEN
        b.placement_fact=DateFact(DatePrecision.MONTH,inherited_conf,year=ka[0],month=ka[1],source='live-photo-companion-placement',note='Folder placement inherited from content-identifier-proven Live Photo still; component capture metadata remains unchanged')
    elif kb and not ka:
        b.placement_fact=b.date_fact
        inherited_conf=b.date_fact.confidence if b.date_fact else Confidence.PROVEN
        a.placement_fact=DateFact(DatePrecision.MONTH,inherited_conf,year=kb[0],month=kb[1],source='live-photo-companion-placement',note='Folder placement inherited from content-identifier-proven Live Photo movie; component capture metadata remains unchanged')
    else:
        a.placement_fact=a.date_fact;b.placement_fact=b.date_fact
    a.output_dir=placement_dir(a.placement_fact);b.output_dir=placement_dir(b.placement_fact)
    if record_relation:
        rel={'kind':relation.kind,'members':relation.member_ids,'confidence':relation.confidence.value,'content_identifier':relation.key}
        for x in (a,b):
            if rel not in x.family_relations:x.family_relations.append(rel)


def reapply_proven_live_photo_placements(assets:list[object])->int:
    """Reconcile proven Live Photo placement after explicit user decisions.

    Planning discovers/records the relationship first.  A later user decision may
    change one component's effective month, so the proven compound-family placement
    must be recomputed before filenames are finalized.  Returns the number of unique
    proven pairs re-evaluated.
    """
    by_id={a.asset_id:a for a in assets};seen=set();count=0
    for a in assets:
        for rel in getattr(a,'family_relations',[]) or []:
            if rel.get('kind')!='apple-live-photo' or rel.get('confidence')!=Confidence.PROVEN.value:continue
            ids=tuple(rel.get('members') or [])
            if len(ids)!=2 or ids in seen or tuple(reversed(ids)) in seen:continue
            seen.add(ids)
            relation=FamilyRelation('apple-live-photo',list(ids),Confidence.PROVEN,rel.get('content_identifier') or rel.get('key'))
            apply_live_photo_placement(by_id,relation,record_relation=False);count+=1
    return count

_EDIT_SUFFIXES=(
    '-edited','-modified','-modifié','-modifie','-bearbeitet','-bewerkt','-editado','-editada',
    '-modificato','-modificata','-redigerad','-muokattu','-edytowane','-edytowany',
)


def edited_base_stem(filename:str)->str|None:
    p=PurePosixPath(filename);stem=p.stem;low=stem.casefold()
    for suffix in _EDIT_SUFFIXES:
        if low.endswith(suffix.casefold()) and len(stem)>len(suffix):return stem[:-len(suffix)]
    return None


def variant_hints(evidence:list[EmbeddedEvidence])->list[FamilyRelation]:
    """Return non-authoritative variant hints; never deduplicate or change dates."""
    by_dir_stem={}
    # filename in EmbeddedEvidence may include a logical path or basename.
    for e in evidence:
        p=PurePosixPath(e.filename);by_dir_stem.setdefault((str(p.parent),p.stem.casefold()),[]).append(e)
    out=[];seen=set()
    # Google edited/modified suffix hints.
    for e in evidence:
        p=PurePosixPath(e.filename);base=edited_base_stem(p.name)
        if not base:continue
        originals=by_dir_stem.get((str(p.parent),base.casefold()),[])
        for o in originals:
            if o.asset_id==e.asset_id:continue
            key=tuple(sorted((o.asset_id,e.asset_id)))
            if key in seen:continue
            seen.add(key);out.append(FamilyRelation('edited-variant-hint',list(key),Confidence.PROBABLE,key='filename-suffix',warnings=['Filename pattern suggests an edited variant; relationship is not authoritative without additional evidence or user confirmation.']))
    # RAW + rendered still with the same stem in the same logical directory.
    groups={}
    for e in evidence:
        p=PurePosixPath(e.filename);groups.setdefault((str(p.parent),p.stem.casefold()),[]).append(e)
    for rows in groups.values():
        raws=[e for e in rows if PurePosixPath(e.filename).suffix.lower() in RAW_SUFFIXES]
        renders=[e for e in rows if PurePosixPath(e.filename).suffix.lower() in STILL_SUFFIXES]
        if raws and renders:
            ids=sorted({e.asset_id for e in raws+renders});key=tuple(ids)
            if key not in seen:
                seen.add(key);out.append(FamilyRelation('raw-rendered-hint',ids,Confidence.PROBABLE,key='same-directory-stem',warnings=['Same stem suggests RAW+rendered companions; bytes remain separate and metadata is not inherited automatically.']))
    return out
