from __future__ import annotations
from dataclasses import dataclass, asdict, field
from pathlib import Path
import json
from .audit import audit_archive, AuditReport

@dataclass
class MigrationVerification:
    schema:str='gpa.verification.v4'
    runs_checked:int=0
    source_archives_fingerprinted:int=0
    source_integrity_clean:bool=False
    takeout_sequence_known_gaps:int=0
    takeout_receipts_verified:bool=False
    takeout_receipts_checked:int=0
    exit_evidence_verified:bool=False
    output_integrity_clean:bool=False
    assets_planned:int=0
    assets_committed:int=0
    unclassified_preserved:int=0
    unresolved_run_failures:int=0
    partial_runs:int=0
    review_runs:int=0
    archive_audit_ok:bool=False
    export_completeness_independently_proven:bool=False
    archive_integrity_verified:bool=False
    source_vault_verified:bool=False
    source_vault_records:int=0
    source_vault_redundant_verified:bool=False
    source_vault_redundant_files_checked:int=0
    source_vault_redundant_separate_volume_proven:bool=False
    source_vault_redundant_separate_disk_numbers_proven:bool=False
    source_vault_redundant_separate_physical_device_proven:bool=False
    source_vault_redundant_separate_media_confirmed:bool=False
    source_vault_redundant_storage_identity:dict|None=None
    redundant_copy_verified:bool=False
    redundant_copy_files_checked:int=0
    redundant_copy_separate_volume_proven:bool=False
    redundant_copy_separate_disk_numbers_proven:bool=False
    redundant_copy_separate_physical_device_proven:bool=False
    redundant_copy_separate_media_confirmed:bool=False
    redundant_copy_storage_identity:dict|None=None
    organization_complete:bool=False
    identity_review_assets:int=0
    media_validation_passed:int=0
    media_validation_failed:int=0
    media_validation_unavailable:int=0
    media_validation_complete:bool=False
    archive_format_status:str='unknown'
    archive_format_id:str|None=None
    archive_format_supported:bool=False
    google_retirement_ready:bool=False
    blockers:list[str]=field(default_factory=list)
    notes:list[str]=field(default_factory=list)

    def to_dict(self):return asdict(self)


def _load_runs(root:Path)->list[dict]:
    runs=root/'metadata'/'runs';out=[]
    if not runs.exists():return out
    for p in sorted(runs.glob('*.json')):
        try:
            obj=json.loads(p.read_text(encoding='utf-8'));obj['_path']=str(p)
            out.append(obj)
        except Exception:
            out.append({'_path':str(p),'status':'partial','failures':[{'error':'unreadable run manifest'}]})
    return out


def _count_review_assets(root:Path)->int:
    count=0;assets=root/'metadata'/'assets'
    if not assets.exists():return 0
    for d in assets.iterdir():
        if not d.is_dir():continue
        try:
            ptr=json.loads((d/'current.json').read_text(encoding='utf-8'));rid=ptr['revision_id']
            rec=json.loads((d/'revisions'/f'{rid}.json').read_text(encoding='utf-8'))
        except Exception:
            count+=1;continue
        date=rec.get('placement_date') or rec.get('date') or {}
        conf=date.get('confidence');prec=date.get('precision')
        warnings=rec.get('warnings') or []
        if conf in {'unknown','conflict','probable'} or prec in {'unknown','range','year'} or any('review' in str(w).lower() for w in warnings):count+=1
    return count


def _count_identity_review_assets(root:Path)->int:
    from .identity_observations import IdentityObservationStore
    assets=root/'metadata'/'assets';store=IdentityObservationStore(root);count=0
    if not assets.exists():return 0
    for d in assets.iterdir():
        if d.is_dir() and store.summary(d.name).needs_identity_review:count+=1
    return count


def verify_migration(root:Path, *, all_takeout_parts_confirmed:bool=False, redundant_copy_verified:bool=False, redundant_copy_root:Path|None=None, source_vault_root:Path|None=None, source_vault_redundant_copy_root:Path|None=None, redundant_copy_separate_media_confirmed:bool=False, source_vault_redundant_separate_media_confirmed:bool=False, require_independent_media:bool=True, require_source_vault:bool=True, require_source_vault_redundancy:bool=True, takeout_receipts=None, require_takeout_receipt:bool=True, exit_evidence_path:Path|None=None, require_exit_evidence:bool=True, require_media_validation:bool=True, require_archive_format:bool=True)->MigrationVerification:
    """Summarize evidence needed before retiring the cloud source.

    This deliberately separates *archive integrity* from *export completeness*.
    Raw Takeout files alone cannot prove that the user downloaded every archive part
    Google intended to provide.  That requires an external/user confirmation.
    """
    root=Path(root);runs=_load_runs(root);audit=audit_archive(root);v=MigrationVerification()
    v.runs_checked=len(runs);v.archive_audit_ok=audit.ok;v.output_integrity_clean=audit.ok
    v.archive_format_status=audit.archive_format_status;v.archive_format_id=audit.archive_format_id;v.archive_format_supported=audit.archive_format_supported
    v.media_validation_passed=audit.media_validation_passed;v.media_validation_failed=audit.media_validation_failed;v.media_validation_unavailable=audit.media_validation_unavailable
    v.media_validation_complete=(audit.media_validation_failed==0 and audit.media_validation_unavailable==0 and audit.media_validation_passed==audit.assets_checked)
    source_problems=0;fingerprints=0;bad_fps=0;failures=0;referenced_source_hashes=set();referenced_source_paths=[];run_sources=[]
    for r in runs:
        v.assets_planned+=int(r.get('assets_planned') or 0);v.assets_committed+=int(r.get('assets_committed') or 0)
        failures+=len(r.get('failures') or []);source_problems+=len(r.get('source_problems') or [])
        if r.get('status')=='partial':v.partial_runs+=1
        if r.get('status')=='needs_review':v.review_runs+=1
        v.unclassified_preserved+=len(r.get('unclassified_preserved') or [])
        for a in r.get('source_archives') or []:
            run_sources.append(a)
            if a.get('path'):referenced_source_paths.append(a.get('path'))
            if a.get('sha256'):
                fingerprints+=1;referenced_source_hashes.add(a.get('sha256'))
            else:bad_fps+=1
    v.source_archives_fingerprinted=fingerprints
    v.unresolved_run_failures=failures
    v.source_integrity_clean=bool(runs) and source_problems==0 and bad_fps==0 and v.partial_runs==0
    try:
        from .takeout_parts import analyze_takeout_parts
        pa=analyze_takeout_parts(referenced_source_paths);v.takeout_sequence_known_gaps=pa.known_gaps
        if not pa.ok:v.source_integrity_clean=False
    except Exception as e:
        v.notes.append(f'Takeout part-sequence analysis could not be completed: {e}')
    v.archive_integrity_verified=v.source_integrity_clean and v.output_integrity_clean and failures==0 and v.assets_planned==v.assets_committed
    review_count=_count_review_assets(root);v.identity_review_assets=_count_identity_review_assets(root);v.organization_complete=review_count==0 and v.identity_review_assets==0 and v.review_runs==0 and v.unclassified_preserved==0
    if source_vault_root is not None:
        try:
            from .source_vault import audit_source_vault
            va=audit_source_vault(Path(source_vault_root));v.source_vault_records=va.records_checked
            missing=sorted(referenced_source_hashes-set(va.digests))
            v.source_vault_verified=va.ok and va.records_checked>0 and va.archives_checked>0 and not missing
            if not va.ok:v.notes.append(f'Source vault audit found {len(va.problems)} problem(s).')
            if missing:v.notes.append(f'Source vault is missing {len(missing)} Takeout archive fingerprint(s) referenced by migration runs.')
        except Exception as e:
            v.notes.append(f'Source vault audit could not be completed: {e}')
    # Durable download receipts close the filename-only blind spot where 001,002
    # cannot reveal that Google also offered 003.  The expected count is captured
    # from the Takeout UI/email while the local part hashes bind that confirmation
    # to the exact archives later used by migration runs.
    if takeout_receipts:
        try:
            from .takeout_receipt import verify_takeout_receipts
            rr=verify_takeout_receipts(takeout_receipts,run_sources)
            v.takeout_receipts_checked=rr.receipts_checked;v.takeout_receipts_verified=rr.ok
            if not rr.ok:v.notes.extend(rr.problems)
        except Exception as e:
            v.notes.append(f'Takeout receipt verification could not be completed: {e}')
    try:
        from .exit_evidence import verify_exit_evidence
        ok,problems=verify_exit_evidence(exit_evidence_path)
        v.exit_evidence_verified=ok
        if not ok:v.notes.extend(problems)
    except Exception as e:
        v.notes.append(f'Google Photos exit checklist verification could not be completed: {e}')
    if source_vault_root is not None and source_vault_redundant_copy_root is not None:
        try:
            from .mirror import build_tree_manifest,verify_mirror
            from .storage_identity import assess_storage_independence
            si=assess_storage_independence(Path(source_vault_root),Path(source_vault_redundant_copy_root),separate_physical_media_confirmed=source_vault_redundant_separate_media_confirmed)
            v.source_vault_redundant_separate_volume_proven=si.separate_volume_proven
            v.source_vault_redundant_separate_disk_numbers_proven=si.separate_disk_numbers_proven
            v.source_vault_redundant_separate_physical_device_proven=si.separate_physical_device_proven
            v.source_vault_redundant_separate_media_confirmed=si.separate_physical_media_confirmed
            v.source_vault_redundant_storage_identity=si.to_dict()
            if si.same_resolved_path or si.same_volume is True or si.same_physical_device is True:
                v.notes.append(si.note)
            manifest=build_tree_manifest(Path(source_vault_root))
            vr=verify_mirror(manifest,Path(source_vault_redundant_copy_root),report_extras=False)
            v.source_vault_redundant_verified=vr.ok and not si.same_resolved_path
            v.source_vault_redundant_files_checked=vr.files_checked
            if not vr.ok:v.notes.append(f'Source-vault redundant-copy audit found {len(vr.missing)} missing and {len(vr.mismatched)} mismatched file(s).')
        except Exception as e:
            v.notes.append(f'Source-vault redundant-copy audit could not be completed: {e}')
    v.export_completeness_independently_proven=False
    if redundant_copy_root is not None:
        try:
            from .mirror import build_tree_manifest,verify_mirror
            from .storage_identity import assess_storage_independence
            si=assess_storage_independence(root,Path(redundant_copy_root),separate_physical_media_confirmed=redundant_copy_separate_media_confirmed)
            v.redundant_copy_separate_volume_proven=si.separate_volume_proven
            v.redundant_copy_separate_disk_numbers_proven=si.separate_disk_numbers_proven
            v.redundant_copy_separate_physical_device_proven=si.separate_physical_device_proven
            v.redundant_copy_separate_media_confirmed=si.separate_physical_media_confirmed
            v.redundant_copy_storage_identity=si.to_dict()
            if si.same_resolved_path or si.same_volume is True or si.same_physical_device is True:v.notes.append(si.note)
            manifest=build_tree_manifest(root,exclude_names={'verification.json'})
            mr=verify_mirror(manifest,Path(redundant_copy_root),report_extras=False)
            v.redundant_copy_verified=mr.ok and not si.same_resolved_path
            v.redundant_copy_files_checked=mr.files_checked
            if not mr.ok:v.notes.append(f'Redundant-copy audit found {len(mr.missing)} missing and {len(mr.mismatched)} mismatched file(s).')
        except Exception as e:
            v.notes.append(f'Redundant-copy audit could not be completed: {e}')
    else:
        v.redundant_copy_verified=bool(redundant_copy_verified)
    if not runs:v.blockers.append('No durable migration run manifests were found.')
    if not v.source_integrity_clean:v.blockers.append('One or more source archives/run inputs have integrity, availability, or known Takeout part-sequence problems.')
    if v.takeout_sequence_known_gaps:v.blockers.append(f'Takeout archive filenames reveal {v.takeout_sequence_known_gaps} known missing internal/expected part(s).')
    if not v.output_integrity_clean:v.blockers.append('Independent archive audit found output/history integrity problems.')
    if failures or v.assets_planned!=v.assets_committed:v.blockers.append('Not every planned asset was committed successfully.')
    if review_count:v.notes.append(f'{review_count} current asset(s) still need organization/metadata review; preserved data may still be intact.')
    if v.identity_review_assets:v.notes.append(f'{v.identity_review_assets} content object(s) retain unresolved logical-item identity multiplicity; all observed evidence remains preserved locally for offline review.')
    if v.unclassified_preserved:v.notes.append(f'{v.unclassified_preserved} unclassified Takeout member(s) were preserved byte-for-byte but are not yet understood.')
    v.notes.append('Takeout ZIP contents alone cannot independently prove that every archive part Google offered was downloaded.')
    if not all_takeout_parts_confirmed:v.blockers.append('User/external confirmation that every Google Takeout archive part was downloaded is still required.')
    if require_takeout_receipt and not v.takeout_receipts_verified:v.blockers.append('A durable Takeout receipt binding the confirmed expected part count to the exact downloaded archive hashes is still required.')
    if require_exit_evidence and not v.exit_evidence_verified:v.blockers.append('The Google Photos exit checklist is incomplete or missing (shared albums, partner sharing, Locked Folder, Takeout scope, and late changes).')
    if require_source_vault and not v.source_vault_verified:v.blockers.append('The raw Google Takeout source archives have not yet been preserved and independently verified in the source vault.')
    if require_source_vault and require_source_vault_redundancy and not v.source_vault_redundant_verified:v.blockers.append('A separately hash-verified redundant copy of the raw Takeout source vault is still required before cloud deletion is recommended.')
    if not v.redundant_copy_verified:v.blockers.append('At least one separately hash-verified redundant copy of the finished archive is still required before cloud deletion is recommended.')
    if require_archive_format and not v.archive_format_supported:v.blockers.append('The archive does not have a supported versioned GPA archive-format manifest; upgrade/qualification is required before Google retirement.')
    if require_media_validation and not v.media_validation_complete:v.blockers.append('Not every current media asset has passed a qualified read-only media/container validator with recorded version provenance.')
    if require_independent_media and v.redundant_copy_verified and not v.redundant_copy_separate_media_confirmed:
        v.blockers.append('The redundant finished-archive copy has not been confirmed to be on separate physical media.')
    if require_independent_media and require_source_vault and require_source_vault_redundancy and v.source_vault_redundant_verified and not v.source_vault_redundant_separate_media_confirmed:
        v.blockers.append('The redundant raw-source-vault copy has not been confirmed to be on separate physical media.')
    source_gate=(v.source_vault_verified or not require_source_vault)
    archive_media_gate=(not require_independent_media or v.redundant_copy_separate_media_confirmed)
    source_media_gate=(not require_independent_media or not (require_source_vault and require_source_vault_redundancy) or v.source_vault_redundant_separate_media_confirmed)
    source_redundancy_gate=(v.source_vault_redundant_verified or not (require_source_vault and require_source_vault_redundancy))
    receipt_gate=(v.takeout_receipts_verified or not require_takeout_receipt)
    exit_gate=(v.exit_evidence_verified or not require_exit_evidence)
    v.export_completeness_independently_proven=bool(all_takeout_parts_confirmed and receipt_gate and exit_gate and v.takeout_sequence_known_gaps==0)
    media_validation_gate=(v.media_validation_complete or not require_media_validation)
    archive_format_gate=(v.archive_format_supported or not require_archive_format)
    v.google_retirement_ready=v.archive_integrity_verified and all_takeout_parts_confirmed and receipt_gate and exit_gate and v.redundant_copy_verified and archive_media_gate and source_gate and source_redundancy_gate and source_media_gate and media_validation_gate and archive_format_gate
    if v.google_retirement_ready:
        v.notes.append('Integrity and redundancy gates are satisfied; unresolved organization metadata does not by itself imply data loss.')
    return v


def write_verification_report(root:Path,report:MigrationVerification,path:Path|None=None)->Path:
    root=Path(root);path=path or root/'metadata'/'verification.json';path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(report.to_dict(),indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8');tmp.replace(path);return path
