from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import hashlib, json

from .ledger import Ledger
from .identity_observations import IdentityObservationStore
from .model import DateFact, GoogleSidecar, SourceRef
from .planner import LogicalAsset
from .revisions import RevisionStore
from .transactions import commit_no_overwrite, sha256_file, stage_bytes, ProjectionConflict
from .writepolicy import plan_metadata
from .media_validation import validate_media, MediaValidation
from .xmp import render_xmp_sidecar
from .zipindex import Member, SourceIntegrityError, ZipLibrary

class MaterializeError(Exception):pass


def _date_record(f:DateFact|None)->dict|None:
    if not f:return None
    return {
        'precision':f.precision.value,'confidence':f.confidence.value,
        'value':f.value.isoformat() if f.value else None,
        'year':f.year,'month':f.month,
        'start':f.start.isoformat() if f.start else None,
        'end':f.end.isoformat() if f.end else None,
        'timezone_known':f.timezone_known,'source':f.source,'note':f.note,
    }

def _consensus_sidecar(values:list[GoogleSidecar])->GoogleSidecar|None:
    """Return only Google fields on which every manifestation agrees.

    Album/year copies of the same media may differ in volatile raw JSON while still
    agreeing on portable user metadata such as description.  Conversely, when exact
    bytes may represent multiple logical items, a disputed description/location must
    never be projected into the one content-level XMP sidecar.
    """
    if not values:return None
    def same(attr):
        vals=[getattr(v,attr) for v in values]
        return vals[0] if all(v==vals[0] for v in vals[1:]) else None
    people=[list(v.people) for v in values]
    agreed_people=people[0] if all(v==people[0] for v in people[1:]) else []
    return GoogleSidecar(
        title=same('title'),taken_epoch=same('taken_epoch'),creation_epoch=same('creation_epoch'),
        description=same('description'),geo_current=same('geo_current'),geo_exif=same('geo_exif'),
        favorited=same('favorited'),people=agreed_people,url=same('url'),origin={},raw={},
    )

def xmp_path_for(media_path:Path)->Path:
    return media_path.with_name(media_path.name+'.xmp')

class ArchiveMaterializer:
    def __init__(self,root:Path,lib:ZipLibrary,ledger:Ledger|None=None,*,read_only:bool=False):
        self.root=Path(root);self.lib=lib
        if not lib.members:lib.scan()
        self.members={(m.archive,m.path):m for m in lib.members}
        self._ledger_path=self.root/'metadata'/'operations.sqlite'
        self.ledger=ledger if ledger is not None else (None if read_only else Ledger(self._ledger_path))
        self.revisions=RevisionStore(self.root)
        self.identity_observations=IdentityObservationStore(self.root)

    def _ledger(self)->Ledger:
        # Read-only planning may share this object, but materialization itself is
        # an explicit mutation and may therefore initialize the operational ledger.
        if self.ledger is None:self.ledger=Ledger(self._ledger_path)
        return self.ledger

    def _member_for(self,ref:SourceRef)->Member:
        try:return self.members[(ref.archive,ref.path)]
        except KeyError as e:raise MaterializeError(f'source occurrence not found: {ref.archive}:{ref.path}') from e

    def _extract_verified(self,asset:LogicalAsset,dest_dir:Path)->Path:
        failures=[]
        for ref in asset.occurrences:
            m=self._member_for(ref)
            try:
                staged,digest=self.lib.extract_to_staging(m,dest_dir,asset.sha256)
                return staged
            except Exception as e:
                failures.append(f'{ref.archive}:{ref.path}: {e}')
        raise MaterializeError('all exact-duplicate sources failed: '+' | '.join(failures))

    def build_record(self,asset:LogicalAsset,media_path:Path,xmp_path:Path|None,xmp_sha256:str|None=None,media_validation:MediaValidation|dict|None=None)->dict:
        if media_validation is None and media_path.exists():media_validation=validate_media(media_path,expected_sha256=asset.sha256)
        validation_record=media_validation.to_dict() if isinstance(media_validation,MediaValidation) else media_validation
        return {
            'schema':'gpa.asset.v1',
            'asset_id':asset.asset_id,
            'media_sha256':asset.sha256,
            'projection':{'media':str(media_path.relative_to(self.root)).replace('\\','/'),
                          'xmp':str(xmp_path.relative_to(self.root)).replace('\\','/') if xmp_path else None,
                          'xmp_sha256':xmp_sha256},
            'date':_date_record(asset.date_fact),
            'placement_date':_date_record(asset.placement_fact),
            'family_relations':asset.family_relations,
            'location':({'confidence':asset.location_fact.confidence.value,'lat':asset.location_fact.lat,'lon':asset.location_fact.lon,'source':asset.location_fact.source,'current_candidates':asset.location_fact.current_candidates,'original_candidates':asset.location_fact.original_candidates,'embedded_candidates':asset.location_fact.embedded_candidates,'warnings':asset.location_fact.warnings} if asset.location_fact else None),
            'place':(asdict(asset.place_evidence) if asset.place_evidence else None),
            'timezone':({'zone':asset.timezone_evidence.zone,'confidence':asset.timezone_evidence.confidence.value,'dataset':asset.timezone_evidence.dataset,'note':asset.timezone_evidence.note,'tzdata_version':asset.timezone_evidence.tzdata_version,'suggested_local_time':asset.timezone_suggestion} if asset.timezone_evidence else None),
            'user_location_label':asset.user_location_label,
            'user_decisions':list(asset.user_decisions),
            'occurrences':[{'archive':r.archive,'path':r.path} for r in asset.occurrences],
            'occurrence_contexts':list(asset.occurrence_contexts),
            'content_identity':asset.content_identity.to_dict() if asset.content_identity else None,
            'google_sidecars':[{'archive':r.archive,'path':r.path} for r in asset.sidecars],
            'albums':[{'album_id':a.album_id,'logical_dir':a.logical_dir,'title':a.title,'sources':[{'archive':r.archive,'path':r.path} for r in a.sources],'raw_records':a.raw_records,'warnings':a.warnings} for a in asset.albums],
            'google_raw':[v.raw for v in asset.sidecar_values],
            'embedded':({'capture_local':asset.embedded.capture_local.isoformat() if asset.embedded and asset.embedded.capture_local else None,'offset_seconds':asset.embedded.offset.total_seconds() if asset.embedded and asset.embedded.offset is not None else None,'gps':asset.embedded.gps if asset.embedded else None,'make':asset.embedded.make if asset.embedded else None,'model':asset.embedded.model if asset.embedded else None,'content_identifier':asset.embedded.content_identifier if asset.embedded else None,'warnings':asset.embedded.warnings if asset.embedded else [],'raw':asset.embedded.raw if asset.embedded else {}} if asset.embedded else None),
            'media_validation':validation_record,
            'warnings':list(asset.warnings),
        }

    def materialize(self,asset:LogicalAsset)->dict:
        if not asset.output_dir or not asset.output_name:raise MaterializeError('asset has no output placement')
        dest=self.root/asset.output_dir/asset.output_name
        ledger=self._ledger();ledger.set(asset.asset_id,'DISCOVERED',dest_path=dest)
        if dest.exists():
            if sha256_file(dest)!=asset.sha256:raise ProjectionConflict(f'projection media collision: {dest}')
        else:
            staged=self._extract_verified(asset,dest.parent)
            ledger.set(asset.asset_id,'STAGED',staged,dest)
            commit_no_overwrite(staged,dest)
        if sha256_file(dest)!=asset.sha256:raise MaterializeError('committed media verification failed')
        ledger.set(asset.asset_id,'VERIFIED',dest_path=dest)

        sc=_consensus_sidecar(asset.sidecar_values)
        gps=(asset.location_fact.lat,asset.location_fact.lon) if asset.location_fact and asset.location_fact.exact else None
        plan=plan_metadata(asset.date_fact,sc,location_label=asset.user_location_label,exact_gps=gps)
        xmp_bytes=render_xmp_sidecar(plan)
        xmp_path=None
        if xmp_bytes is not None:
            xmp_path=xmp_path_for(dest);expected=hashlib.sha256(xmp_bytes).hexdigest()
            if xmp_path.exists():
                if sha256_file(xmp_path)!=expected:raise ProjectionConflict(f'projection XMP collision: {xmp_path}')
            else:
                staged=stage_bytes(xmp_bytes,xmp_path.parent);commit_no_overwrite(staged,xmp_path)
        validation=validate_media(dest,expected_sha256=asset.sha256)
        rec=self.build_record(asset,dest,xmp_path,sha256_file(xmp_path) if xmp_path else None,validation)
        rev=self.revisions.add_revision(asset.asset_id,rec)
        self.identity_observations.observe(asset,rev.stem)
        ledger.set(asset.asset_id,'COMMITTED',dest_path=dest)
        return {'asset_id':asset.asset_id,'media':dest,'xmp':xmp_path,'revision':rev,'media_validation':validation.to_dict()}
