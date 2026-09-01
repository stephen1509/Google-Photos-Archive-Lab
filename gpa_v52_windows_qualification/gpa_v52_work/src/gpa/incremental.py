from __future__ import annotations
import hashlib, os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .materializer import ArchiveMaterializer, MaterializeError, xmp_path_for, _consensus_sidecar
from .planner import LogicalAsset
from .revisions import RevisionStore
from .transactions import (
    ProjectionBytes, ProjectionCopy, ProjectionConflict, CommitError,
    apply_projection_update, commit_no_overwrite, fsync_dir, sha256_file, stage_bytes,
)
from .writepolicy import plan_metadata
from .xmp import render_xmp_sidecar

class IncrementalError(Exception):pass

@dataclass(frozen=True)
class IncrementalResult:
    asset_id:str
    action:str
    media:Path
    xmp:Path|None
    revision:Path

@dataclass(frozen=True)
class IncrementalPlan:
    """Read-only description of what applying an asset would do."""
    asset_id:str
    action:str
    media:Path
    xmp:Path|None
    xmp_bytes:bytes|None
    record:dict
    revision_id:str
    old_media:Path|None=None
    old_xmp:Path|None=None
    old_record:dict|None=None


def _rel(root:Path,path:Path|None)->str|None:
    return str(path.relative_to(root)).replace('\\','/') if path else None


def _atomic_replace_bytes(path:Path,data:bytes,expected_old_sha:str|None)->None:
    """Idempotently replace a mutable *projection* file.

    Immutable history must be archived by the caller first.  If a crash occurs
    after os.replace but before the current revision pointer advances, rerunning
    accepts the already-new bytes and completes the metadata commit.
    """
    new_sha=hashlib.sha256(data).hexdigest();path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        got=sha256_file(path)
        if got==new_sha:return
        if expected_old_sha is None or got!=expected_old_sha:
            raise ProjectionConflict(f'current projection sidecar changed unexpectedly: {path}')
        staged=stage_bytes(data,path.parent)
        try:
            os.replace(staged,path);fsync_dir(path.parent)
        finally:
            staged.unlink(missing_ok=True)
        if sha256_file(path)!=new_sha:raise CommitError(f'projection replacement verification failed: {path}')
    else:
        staged=stage_bytes(data,path.parent);commit_no_overwrite(staged,path)
        if sha256_file(path)!=new_sha:raise CommitError(f'projection creation verification failed: {path}')


class IncrementalUpdater:
    """Apply a later Takeout's state to an existing archive without losing history."""
    def __init__(self,root:Path,materializer:ArchiveMaterializer):
        self.root=Path(root);self.materializer=materializer;self.revisions=RevisionStore(self.root)

    def _desired(self,asset:LogicalAsset,current:dict|None=None)->tuple[Path,Path|None,bytes|None,dict]:
        if not asset.output_dir or not asset.output_name:raise IncrementalError('asset has no output placement')
        media=self.root/asset.output_dir/asset.output_name
        sc=_consensus_sidecar(asset.sidecar_values)
        gps=(asset.location_fact.lat,asset.location_fact.lon) if asset.location_fact and asset.location_fact.exact else None
        xmp_bytes=render_xmp_sidecar(plan_metadata(asset.date_fact,sc,exact_gps=gps))
        xp=xmp_path_for(media) if xmp_bytes is not None else None

        # Media-validation evidence is about the exact content SHA-256, not its path.
        # During a relocation the desired destination does not exist yet, so blindly
        # rebuilding the record would drop otherwise valid validation provenance.
        # Reuse the current immutable evidence only when it is bound to these exact
        # bytes. Legacy/unbound evidence is intentionally not promoted here.
        validation=None
        if current and current.get('media_sha256')==asset.sha256:
            mv=current.get('media_validation')
            if isinstance(mv,dict) and mv.get('media_sha256')==asset.sha256 and mv.get('bytes_unchanged') is True:
                validation=mv
        rec=self.materializer.build_record(asset,media,xp,hashlib.sha256(xmp_bytes).hexdigest() if xmp_bytes is not None else None,validation)
        return media,xp,xmp_bytes,rec

    def inspect(self,asset:LogicalAsset)->IncrementalPlan:
        """Classify an update without writing revision/projection state.

        This is used by storage preflight and dry-run reporting. It intentionally
        performs the same desired-record construction as :meth:`apply` but makes
        no filesystem mutations.
        """
        current=self.revisions.current_record(asset.asset_id)
        new_media,new_xmp,new_xmp_bytes,new_record=self._desired(asset,current)
        rid=self.revisions.revision_id(new_record)
        if current is None:
            return IncrementalPlan(asset.asset_id,'new',new_media,new_xmp,new_xmp_bytes,new_record,rid)
        if current.get('media_sha256')!=asset.sha256:
            raise IncrementalError('asset-id collision or corrupted registry: SHA-256 differs')
        old_proj=current.get('projection') or {};old_media_rel=old_proj.get('media')
        if not old_media_rel:raise IncrementalError('current revision has no media projection')
        old_media=self.root/old_media_rel;old_xmp=self.root/old_proj['xmp'] if old_proj.get('xmp') else None
        old_rid=current.get('revision_id')
        if old_rid==rid and old_media.resolve()==new_media.resolve():action='unchanged'
        elif old_media.resolve()!=new_media.resolve():action='relocated'
        else:action='metadata_update'
        return IncrementalPlan(asset.asset_id,action,new_media,new_xmp,new_xmp_bytes,new_record,rid,old_media,old_xmp,current)

    def apply(self,asset:LogicalAsset,after_projection:Callable[[],None]|None=None)->IncrementalResult:
        current=self.revisions.current_record(asset.asset_id)
        if current is None:
            out=self.materializer.materialize(asset)
            return IncrementalResult(asset.asset_id,'new',out['media'],out['xmp'],out['revision'])
        if current.get('media_sha256')!=asset.sha256:
            raise IncrementalError('asset-id collision or corrupted registry: SHA-256 differs')

        old_proj=current.get('projection') or {}
        old_media_rel=old_proj.get('media')
        if not old_media_rel:raise IncrementalError('current revision has no media projection')
        old_media=self.root/old_media_rel
        old_xmp=self.root/old_proj['xmp'] if old_proj.get('xmp') else None
        new_media,new_xmp,new_xmp_bytes,new_record=self._desired(asset,current)

        # Persist the intended metadata revision first, but do NOT make it current yet.
        # A crash leaves the old current pointer valid and the prepared revision harmless.
        revision,rid=self.revisions.prepare_revision(asset.asset_id,new_record)

        if old_media.resolve()!=new_media.resolve():
            generated=[]
            if new_xmp is not None and new_xmp_bytes is not None:
                generated.append(ProjectionBytes(new_xmp,new_xmp_bytes))
            remove=[old_media]
            if old_xmp is not None:remove.append(old_xmp)
            # Preserve an exact prior XMP before it can disappear.
            if old_xmp is not None and old_xmp.exists():
                self.revisions.archive_blob(asset.asset_id,'xmp',old_xmp.read_bytes(),'.xmp')
            apply_projection_update(
                [ProjectionCopy(old_media,new_media,asset.sha256)],generated,remove
            )
        else:
            if not old_media.exists() or sha256_file(old_media)!=asset.sha256:
                raise ProjectionConflict(f'current projection media missing or changed: {old_media}')
            # Same media path: current XMP may be added, replaced, kept, or removed.
            if old_xmp is not None and old_xmp.exists():
                old_bytes=old_xmp.read_bytes();old_sha=hashlib.sha256(old_bytes).hexdigest()
                if new_xmp is None:
                    self.revisions.archive_blob(asset.asset_id,'xmp',old_bytes,'.xmp')
                    old_xmp.unlink();fsync_dir(old_xmp.parent)
                elif old_xmp.resolve()==new_xmp.resolve():
                    if new_xmp_bytes is None:raise IncrementalError('desired XMP path without bytes')
                    new_sha=hashlib.sha256(new_xmp_bytes).hexdigest()
                    if old_sha!=new_sha:
                        self.revisions.archive_blob(asset.asset_id,'xmp',old_bytes,'.xmp')
                        _atomic_replace_bytes(old_xmp,new_xmp_bytes,old_sha)
                else:
                    # Defensive: derived XMP path should follow the media path, but handle
                    # a legacy projection path conservatively if encountered.
                    self.revisions.archive_blob(asset.asset_id,'xmp',old_bytes,'.xmp')
                    if new_xmp_bytes is not None:
                        staged=stage_bytes(new_xmp_bytes,new_xmp.parent);commit_no_overwrite(staged,new_xmp)
                    old_xmp.unlink();fsync_dir(old_xmp.parent)
            elif new_xmp is not None and new_xmp_bytes is not None:
                _atomic_replace_bytes(new_xmp,new_xmp_bytes,None)

        if after_projection:after_projection()
        self.revisions.set_current(asset.asset_id,rid)
        self.materializer.identity_observations.observe(asset,rid)
        # If the prepared record is identical to current, prepare_revision returned the
        # same id and this remains a true no-op from the archive-history perspective.
        old_rid=current.get('revision_id')
        action='unchanged' if old_rid==rid else ('relocated' if old_media.resolve()!=new_media.resolve() else 'metadata_update')
        return IncrementalResult(asset.asset_id,action,new_media,new_xmp,revision)
