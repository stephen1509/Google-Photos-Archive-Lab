from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib, os
from pathlib import Path, PurePosixPath

from .materializer import ArchiveMaterializer
from .incremental import IncrementalUpdater
from .storage import StorageOperation, preflight_operations, IncrementalStoragePreflight
from .output import portable_filename
from .planner import LibraryPlanner, PlanningReport
from .location import LocationPolicy
from .review import DecisionJournal, apply_decision_journal
from .transactions import commit_no_overwrite, stage_bytes, sha256_file, write_json_new
from .revisions import RevisionStore
from .zipindex import SourceRef, SourceProblem
from .archive_format import ensure_archive_for_write, ArchiveFormatError

class RunError(Exception):pass


def _file_sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()

def _safe_unclassified_name(ref:SourceRef,digest:str)->str:
    base=portable_filename(PurePosixPath(ref.path).name or 'unnamed')
    occurrence=hashlib.sha256((ref.archive+'\0'+ref.path).encode('utf-8')).hexdigest()[:10]
    return f'{digest[:16]}__{occurrence}__{base}'

class RunExecutor:
    def __init__(self,archives:list[str|Path],root:Path,*,location_policy:LocationPolicy=LocationPolicy.PREFER_GOOGLE_CURRENT,place_resolver=None,timezone_resolver=None,embedded_provider=None,decision_journal:DecisionJournal|str|Path|None=None,destination_fs_type:str|None=None,storage_reserve_fraction:float=0.05):
        self.archives=[str(Path(a)) for a in archives];self.root=Path(root);self.place_resolver=place_resolver
        self.planner=LibraryPlanner(self.archives,location_policy=location_policy,place_resolver=place_resolver,timezone_resolver=timezone_resolver,embedded_provider=embedded_provider)
        self.decision_journal=decision_journal;self.destination_fs_type=destination_fs_type;self.storage_reserve_fraction=storage_reserve_fraction

    def plan(self)->PlanningReport:
        report=self.planner.plan_report()
        if self.decision_journal:
            apply_decision_journal(report.assets,self.decision_journal,place_resolver=self.place_resolver)
            # Explicit chronology decisions can change one component of a proven
            # compound family. Reconcile placement again without copying metadata.
            from .families import reapply_proven_live_photo_placements
            from .output import resolve_filename_collisions
            reapply_proven_live_photo_placements(report.assets)
            names=resolve_filename_collisions([(a.asset_id,a.output_dir or '',a.output_name or '_') for a in report.assets])
            for a in report.assets:a.output_name=names[a.asset_id]
        return report

    @staticmethod
    def _pretty_json_size(obj:dict)->int:
        import json
        return len((json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8'))

    def storage_preflight(self,report:PlanningReport)->IncrementalStoragePreflight:
        """Estimate *incremental* free-space needs before any archive mutation.

        Existing library bytes are not counted again. New assets add permanent
        media/XMP/history; relocations primarily need transient coexistence space;
        metadata-only updates reserve new history and sidecar staging.
        """
        materializer=ArchiveMaterializer(self.root,self.planner.lib,read_only=True)
        updater=IncrementalUpdater(self.root,materializer);store=RevisionStore(self.root)
        operations=[]
        for asset in report.assets:
            plan=updater.inspect(asset)
            try:
                member=materializer._member_for(asset.occurrences[0]);media_size=int(member.size)
            except Exception:
                media_size=0
            xmp_size=len(plan.xmp_bytes or b'')
            rev_path=self.root/'metadata'/'assets'/asset.asset_id/'revisions'/f'{plan.revision_id}.json'
            rev_size=0 if rev_path.exists() else self._pretty_json_size({'revision_id':plan.revision_id,**plan.record})
            permanent=rev_size;transient=0;written=[]
            if plan.action=='new':
                pointer_size=len((__import__('json').dumps({'revision_id':plan.revision_id},sort_keys=True)+'\n').encode())
                permanent+=media_size+xmp_size+pointer_size
                transient=max(media_size,xmp_size)
                written.extend([media_size,xmp_size])
            elif plan.action=='relocated':
                old_xmp_size=plan.old_xmp.stat().st_size if plan.old_xmp and plan.old_xmp.exists() else 0
                # Relocation keeps one media copy finally, but old+new coexist until commit.
                permanent+=max(0,xmp_size-old_xmp_size)
                transient=media_size+xmp_size
                written.extend([media_size,xmp_size])
                if plan.old_xmp and plan.old_xmp.exists():
                    old_sha=sha256_file(plan.old_xmp);blob=self.root/'metadata'/'assets'/asset.asset_id/'blobs'/'xmp'/f'{old_sha}.xmp'
                    if not blob.exists():permanent+=old_xmp_size
            elif plan.action=='metadata_update':
                old_xmp_size=plan.old_xmp.stat().st_size if plan.old_xmp and plan.old_xmp.exists() else 0
                new_sha=hashlib.sha256(plan.xmp_bytes).hexdigest() if plan.xmp_bytes is not None else None
                old_sha=sha256_file(plan.old_xmp) if plan.old_xmp and plan.old_xmp.exists() else None
                changed=old_sha!=new_sha
                if changed:
                    permanent+=max(0,xmp_size-old_xmp_size);transient=xmp_size;written.append(xmp_size)
                    if old_sha:
                        blob=self.root/'metadata'/'assets'/asset.asset_id/'blobs'/'xmp'/f'{old_sha}.xmp'
                        if not blob.exists():permanent+=old_xmp_size
            operations.append(StorageOperation(asset.asset_id,plan.action,permanent,transient,tuple(x for x in written if x>0)))
        return preflight_operations(self.root,operations,fs_type=self.destination_fs_type,reserve_fraction=self.storage_reserve_fraction)

    def _verify_archive_stats(self,report:PlanningReport)->None:
        for a,expected in report.archive_stats.items():
            st=os.stat(a);current=(st.st_size,st.st_mtime_ns,_file_sha(Path(a)))
            if current!=expected:raise RunError(f'source archive changed after planning: {a}')

    def _archive_fingerprints(self)->list[dict]:
        rows=[]
        for a in self.archives:
            p=Path(a)
            try:
                st=p.stat();digest=_file_sha(p)
                rows.append({'path':a,'size':st.st_size,'mtime_ns':st.st_mtime_ns,'sha256':digest})
            except OSError as e:
                rows.append({'path':a,'error':str(e)})
        return rows

    def _preserve_unclassified(self,report:PlanningReport)->tuple[list[dict],list[dict]]:
        by={(m.archive,m.path):m for m in self.planner.lib.members};saved=[];fail=[]
        droot=self.root/'metadata'/'unclassified';droot.mkdir(parents=True,exist_ok=True)
        for ref in report.unclassified:
            m=by.get((ref.archive,ref.path))
            if not m:
                fail.append({'archive':ref.archive,'path':ref.path,'error':'member not indexed'});continue
            try:
                data=self.planner.lib.read(m);digest=hashlib.sha256(data).hexdigest()
                dest=droot/_safe_unclassified_name(ref,digest)
                if dest.exists():
                    if sha256_file(dest)!=digest:raise RunError(f'unclassified preservation collision: {dest}')
                else:
                    staged=stage_bytes(data,dest.parent);commit_no_overwrite(staged,dest)
                saved.append({'archive':ref.archive,'path':ref.path,'sha256':digest,'saved_as':str(dest.relative_to(self.root)).replace('\\','/')})
            except Exception as e:
                fail.append({'archive':ref.archive,'path':ref.path,'error':str(e)})
        return saved,fail

    def execute(self,report:PlanningReport|None=None,run_id:str|None=None)->dict:
        report=report or self.plan();self._verify_archive_stats(report)
        storage=self.storage_preflight(report)
        if not storage.ok:raise RunError('destination storage preflight failed: '+'; '.join(storage.errors))
        archive_fps=self._archive_fingerprints();self._verify_archive_stats(report)
        try:
            ensure_archive_for_write(self.root)
        except ArchiveFormatError as e:
            raise RunError(str(e)) from e
        materializer=ArchiveMaterializer(self.root,self.planner.lib)
        updater=IncrementalUpdater(self.root,materializer)
        successes=[];failures=[]
        for a in report.assets:
            try:
                if materializer.revisions.current(a.asset_id):
                    got=updater.apply(a)
                    successes.append({'asset_id':a.asset_id,'action':got.action,'media':str(got.media.relative_to(self.root)).replace('\\','/'),'xmp':str(got.xmp.relative_to(self.root)).replace('\\','/') if got.xmp else None,'revision':str(got.revision.relative_to(self.root)).replace('\\','/')})
                else:
                    out=materializer.materialize(a)
                    successes.append({'asset_id':a.asset_id,'action':'new','media':str(out['media'].relative_to(self.root)).replace('\\','/'),'xmp':str(out['xmp'].relative_to(self.root)).replace('\\','/') if out['xmp'] else None,'revision':str(out['revision'].relative_to(self.root)).replace('\\','/'),'media_validation':out.get('media_validation')})
            except Exception as e:
                failures.append({'asset_id':a.asset_id,'error':str(e)})
        preserved,preserve_failures=self._preserve_unclassified(report)
        failures.extend({'unclassified':x} for x in preserve_failures)
        if failures or report.source_problems:status='partial'
        elif report.unclassified:status='needs_review'
        else:status='complete'
        rid=run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        manifest={
            'schema':'gpa.run.v1','run_id':rid,'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':status,'source_archives':archive_fps,
            'source_problems':[asdict(p) for p in report.source_problems],
            'assets_planned':len(report.assets),'assets_committed':len(successes),
            'successes':successes,'failures':failures,
            'unclassified_preserved':preserved,
            'storage_preflight':{
                'required_bytes':storage.required_bytes,'free_bytes':storage.free_bytes,
                'permanent_growth_bytes':storage.permanent_growth_bytes,
                'transient_peak_bytes':storage.transient_peak_bytes,
                'operations':storage.operations,'filesystem_type':storage.filesystem_type,'warnings':storage.warnings,
            },
        }
        path=self.root/'metadata'/'runs'/f'{rid}.json';write_json_new(path,manifest)
        manifest['manifest_path']=str(path)
        return manifest
