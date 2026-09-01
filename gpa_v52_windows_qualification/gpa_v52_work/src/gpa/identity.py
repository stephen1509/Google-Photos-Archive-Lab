from __future__ import annotations
from dataclasses import dataclass, asdict, field
import hashlib, json
from typing import Iterable
from .model import GoogleSidecar


def _canonical_people(values:list[str])->tuple[str,...]:
    return tuple(sorted(str(x) for x in values))


def _material_signature(sc:GoogleSidecar)->str:
    """Fingerprint user-visible item metadata, excluding volatile/undocumented identity hints.

    The signature deliberately ignores title, imageViews, last-modified time and URL.
    Two album/year manifestations of one Google Photos item commonly carry the same
    user-visible metadata even when their filenames differ.  Conversely, materially
    different chronology/location/description/favorite/people evidence must not be
    silently flattened just because the media bytes are identical.
    """
    payload={
        'taken_epoch':sc.taken_epoch,
        'creation_epoch':sc.creation_epoch,
        'description':sc.description,
        'geo_current':sc.geo_current,
        'geo_exif':sc.geo_exif,
        'favorited':sc.favorited,
        'people':_canonical_people(sc.people),
    }
    raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class ItemIdentityCluster:
    cluster_id:str
    material_signature:str
    urls:list[str]=field(default_factory=list)
    sidecar_indexes:list[int]=field(default_factory=list)

    def to_dict(self)->dict:return asdict(self)


@dataclass
class ContentIdentityEvidence:
    """Non-destructive logical-item evidence for one exact media content object.

    SHA-256 is authoritative only for *content* identity.  Takeout does not expose a
    documented stable Google Photos media-item id.  URL equality/difference is useful
    evidence within an export, but is intentionally marked as a hint rather than proof.
    """
    schema:str='gpa.content-identity.v1'
    content_sha256:str=''
    sidecar_count:int=0
    distinct_url_hints:list[str]=field(default_factory=list)
    clusters:list[ItemIdentityCluster]=field(default_factory=list)
    possible_logical_item_count:int=1
    multiplicity_confidence:str='unknown'
    needs_identity_review:bool=False
    notes:list[str]=field(default_factory=list)

    def to_dict(self)->dict:
        d=asdict(self)
        d['clusters']=[x.to_dict() for x in self.clusters]
        return d


def analyze_content_identity(content_sha256:str,sidecars:Iterable[GoogleSidecar])->ContentIdentityEvidence:
    values=list(sidecars);out=ContentIdentityEvidence(content_sha256=content_sha256,sidecar_count=len(values))
    if not values:
        out.notes.append('No Google sidecar item identifier is available; exact SHA-256 proves only content identity.')
        return out

    urls=sorted({x.url for x in values if x.url})
    out.distinct_url_hints=urls
    grouped:dict[str,list[int]]={}
    for i,sc in enumerate(values):grouped.setdefault(_material_signature(sc),[]).append(i)
    for sig,indexes in sorted(grouped.items()):
        cu=sorted({values[i].url for i in indexes if values[i].url})
        cid=hashlib.sha256((content_sha256+'\0'+sig).encode('utf-8')).hexdigest()[:16]
        out.clusters.append(ItemIdentityCluster(cid,sig,cu,indexes))

    # URL is not a documented permanent item ID, but distinct non-empty URLs for
    # identical bytes are strong enough to preserve multiplicity as a review signal.
    # Materially different sidecar metadata is likewise a reason not to silently
    # represent the content as a single logical Google item.
    candidates=max(1,len(out.clusters),len(urls))
    out.possible_logical_item_count=candidates
    if len(urls)>1:
        out.multiplicity_confidence='probable'
        out.needs_identity_review=True
        out.notes.append('Identical media bytes have multiple Google Photos URL hints; Takeout does not document these URLs as stable IDs, so logical-item multiplicity is preserved as probable rather than asserted.')
    elif len(out.clusters)>1:
        out.multiplicity_confidence='probable'
        out.needs_identity_review=True
        out.notes.append('Identical media bytes carry materially different Google sidecar metadata; preserve each evidence cluster and require review rather than flattening the disagreement.')
    elif len(urls)==1:
        out.multiplicity_confidence='probable'
        out.notes.append('Repeated manifestations share one Google Photos URL hint; this supports, but does not prove, that they are one logical Google item.')
    else:
        out.notes.append('No Google Photos URL hint is available; logical-item multiplicity cannot be independently proven from Takeout.')
    return out
