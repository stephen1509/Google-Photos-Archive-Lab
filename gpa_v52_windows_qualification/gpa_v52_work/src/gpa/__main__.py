from __future__ import annotations
import argparse, json, sys
from pathlib import Path


def _emit(obj)->None:
    if hasattr(obj,'to_dict'):obj=obj.to_dict()
    print(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2,default=str))


def _executor(args):
    from .executor import RunExecutor
    return RunExecutor(
        args.input,args.output,decision_journal=getattr(args,'decisions',None),
        destination_fs_type=getattr(args,'fs_type',None),
        storage_reserve_fraction=getattr(args,'reserve_fraction',0.05),
    )


def cmd_preview(args)->int:
    from .preview import build_run_preview
    ex=_executor(args);report=ex.plan();p=build_run_preview(ex,report)
    payload=p.to_dict()
    if args.include_review:
        from .review_queue import build_review_queue
        payload['review_items']=[x.to_dict() for x in build_review_queue(report.assets)]
    _emit(payload);return 0 if p.can_execute else 2


def cmd_run(args)->int:
    ex=_executor(args)
    try:m=ex.execute(run_id=args.run_id)
    except Exception as e:
        _emit({'status':'blocked','error':str(e)});return 2
    _emit(m);return 0 if m.get('status')=='complete' else 1


def cmd_audit(args)->int:
    from .audit import audit_archive
    r=audit_archive(args.output);_emit({'ok':r.ok,'assets_checked':r.assets_checked,'revisions_checked':r.revisions_checked,'blobs_checked':r.blobs_checked,'unclassified_checked':r.unclassified_checked,'identity_observations_checked':r.identity_observations_checked,'identity_sightings_checked':r.identity_sightings_checked,'problems':[vars(x) for x in r.problems]})
    return 0 if r.ok else 1


def cmd_vault(args)->int:
    from .source_vault import vault_source_archives,audit_source_vault
    rows=vault_source_archives(args.input,args.vault);a=audit_source_vault(args.vault)
    _emit({'status':'complete' if a.ok else 'partial','vaulted':[vars(x) for x in rows],'audit':{'ok':a.ok,'records_checked':a.records_checked,'archives_checked':a.archives_checked,'problems':a.problems}})
    return 0 if a.ok else 1


def cmd_backfill_identity(args)->int:
    from .identity_observations import backfill_identity_observations
    r=backfill_identity_observations(args.output);_emit(r);return 0 if not r.get('problems') else 1


def cmd_verify(args)->int:
    from .verification import verify_migration
    v=verify_migration(args.output,all_takeout_parts_confirmed=args.all_parts_confirmed,redundant_copy_verified=args.redundant_copy_verified,redundant_copy_root=args.redundant_copy,source_vault_root=args.source_vault,source_vault_redundant_copy_root=args.source_vault_redundant_copy,redundant_copy_separate_media_confirmed=args.confirm_redundant_copy_separate_media,source_vault_redundant_separate_media_confirmed=args.confirm_source_vault_copy_separate_media,takeout_receipts=args.takeout_receipt,exit_evidence_path=args.exit_evidence)
    _emit(v);return 0 if v.google_retirement_ready else 1


def cmd_receipt(args)->int:
    from .takeout_receipt import create_takeout_receipt,write_takeout_receipt
    r=create_takeout_receipt(args.input,expected_part_count=args.expected_count,export_id=args.export_id,confirmation_source=args.confirmation_source)
    write_takeout_receipt(args.output,r);_emit(r);return 0 if len(r.parts)==r.expected_part_count else 1


def cmd_exit_evidence(args)->int:
    from .exit_evidence import create_exit_evidence,write_exit_evidence
    e=create_exit_evidence(
        shared_albums_reviewed=args.shared_albums_reviewed,
        wanted_shared_media_secured=args.wanted_shared_media_secured,
        partner_sharing_reviewed=args.partner_sharing_reviewed,
        locked_folder_reviewed=args.locked_folder_reviewed,
        takeout_scope_reviewed=args.takeout_scope_reviewed,
        recent_changes_accounted_for=args.recent_changes_accounted_for,
        notes=list(args.note or []),
    )
    write_exit_evidence(args.output,e);_emit(e);return 0 if e.complete else 1




def cmd_admit_exiftool(args)->int:
    from .exiftool_distribution import acquire_distribution_archive,admit_distribution,get_distribution_spec
    from .transactions import write_json_new
    spec=get_distribution_spec(args.candidate)
    if args.archive is not None:
        archive=args.archive;downloaded=False;reused=False;final_url=None
    else:
        if args.cache is None:raise ValueError('--cache is required when --archive is not supplied')
        archive,downloaded,reused,final_url=acquire_distribution_archive(spec,args.cache,timeout_seconds=args.timeout)
    r=admit_distribution(spec,archive,args.prepare,source_url=final_url,downloaded=downloaded,reused_existing=reused)
    if args.report:write_json_new(args.report,r.to_dict())
    _emit(r)
    required=r.archive_verified and (args.prepare is None or r.checks.get('distribution_prepared') is True)
    return 0 if required and not r.errors else 1


def cmd_qualify_exiftool(args)->int:
    from .exiftool_qualification import qualify_exiftool_candidate,write_qualification_report
    r=qualify_exiftool_candidate(args.executable,args.workdir,distribution_root=args.distribution_root,subprocess_timeout_seconds=args.timeout)
    if args.report:write_qualification_report(args.report,r)
    _emit(r);return 0 if r.passed else 1


def cmd_qualify_exiftool_format(args)->int:
    from .format_roundtrip_qualification import qualify_exiftool_format,write_format_qualification_report
    r=qualify_exiftool_format(args.executable,args.workdir,format_profile_id=args.format_profile,distribution_root=args.distribution_root,qualification_fixture=args.fixture,subprocess_timeout_seconds=args.timeout)
    if args.report:write_format_qualification_report(args.report,r)
    _emit(r);return 0 if r.passed else 1


def cmd_qualify_exiftool_relationship(args)->int:
    from .relationship_roundtrip_qualification import qualify_exiftool_motion_photo,qualify_exiftool_live_photo,write_relationship_qualification_report
    if args.relationship_profile == 'motion-photo-composite-v1':
        r=qualify_exiftool_motion_photo(
            args.executable,args.workdir,distribution_root=args.distribution_root,
            format_profile_id=args.format_profile,qualification_fixture=args.fixture,
            subprocess_timeout_seconds=args.timeout,
        )
    elif args.relationship_profile == 'live-photo-pair-v1':
        if args.format_profile != 'jpeg-single-v1' or args.fixture is not None:
            raise ValueError('live-photo-pair-v1 writer qualification currently supports only jpeg-single-v1 + mov-single-v1 and does not accept --fixture')
        r=qualify_exiftool_live_photo(args.executable,args.workdir,distribution_root=args.distribution_root,subprocess_timeout_seconds=args.timeout)
    else:
        raise ValueError(f'unsupported relationship profile: {args.relationship_profile}')
    if args.report:write_relationship_qualification_report(args.report,r)
    _emit(r);return 0 if r.passed else 1


def cmd_bundle_exiftool_relationship_evidence(args)->int:
    from .relationship_evidence import bundle_relationship_evidence_report_files,write_relationship_evidence_bundle
    b=bundle_relationship_evidence_report_files(args.format_evidence,args.relationship_qualification,secondary_format_evidence_path=args.secondary_format_evidence,distribution_root=args.distribution_root,executable_path=args.executable)
    write_relationship_evidence_bundle(args.output,b)
    _emit(b);return 0 if b.ready_for_relationship_promotion_review else 1


def cmd_bundle_exiftool_evidence(args)->int:
    from .exiftool_evidence import bundle_exiftool_report_files,write_exiftool_evidence_bundle
    b=bundle_exiftool_report_files(args.admission,args.qualification,archive_path=args.archive,distribution_root=args.distribution_root,executable_path=args.executable)
    write_exiftool_evidence_bundle(args.output,b)
    _emit(b);return 0 if b.ready_for_promotion_review else 1

def cmd_bundle_exiftool_format_evidence(args)->int:
    from .format_evidence import bundle_format_evidence_report_files,write_format_evidence_bundle
    b=bundle_format_evidence_report_files(args.base_evidence,args.format_qualification,archive_path=args.archive,distribution_root=args.distribution_root,executable_path=args.executable)
    write_format_evidence_bundle(args.output,b)
    _emit(b);return 0 if b.ready_for_format_promotion_review else 1

def cmd_inspect_live_photo_pair(args)->int:
    from .live_photo_integrity import inspect_live_photo_pair
    r=inspect_live_photo_pair(args.still,args.movie,timeout_seconds=args.timeout)
    _emit(r)
    return 0 if r.identity_verified and r.component_health_verified else 1


def cmd_qualify_raw_corpus(args)->int:
    from .raw_corpus import qualify_raw_corpus,write_raw_corpus_qualification_report
    r=qualify_raw_corpus(args.manifest,args.samples_root,timeout_seconds=args.timeout)
    if args.report:write_raw_corpus_qualification_report(args.report,r)
    _emit(r);return 0 if r.passed else 1


def cmd_fetch_raw_corpus(args)->int:
    from .raw_corpus import fetch_raw_corpus,write_raw_corpus_fetch_report
    r=fetch_raw_corpus(args.manifest,args.samples_root,timeout_seconds=args.timeout)
    if args.report:write_raw_corpus_fetch_report(args.report,r)
    _emit(r);return 0 if r.passed else 1


def cmd_qualify_windows_core(args)->int:
    from .windows_qualification import qualify_windows_core,write_windows_core_qualification_report
    r=qualify_windows_core(args.primary,args.backup,args.raw_manifest,args.raw_samples_root,fetch_raw=args.fetch_raw,timeout_seconds=args.timeout)
    if args.report:write_windows_core_qualification_report(args.report,r)
    _emit(r);return 0 if r.passed else 1



def cmd_qualify_windows_release(args)->int:
    from .windows_release_qualification import qualify_windows_release,write_windows_release_qualification_report
    r=qualify_windows_release(
        args.primary,args.backup,args.raw_manifest,args.raw_samples_root,
        exiftool_candidate_id=args.exiftool_candidate,
        exiftool_cache_dir=args.exiftool_cache,
        exiftool_prepare_parent=args.exiftool_prepare,
        exiftool_workdir=args.exiftool_workdir,
        fetch_raw=args.fetch_raw,fetch_exiftool=args.fetch_exiftool,
        timeout_seconds=args.timeout,exiftool_subprocess_timeout_seconds=args.exiftool_timeout,
    )
    if args.report:write_windows_release_qualification_report(args.report,r)
    _emit(r);return 0 if r.passed else 1

def cmd_qualify_windows_writer(args)->int:
    from .windows_writer_qualification import qualify_windows_writer_review,write_windows_writer_qualification_report
    profiles=tuple(args.format_profile) if args.format_profile else None
    kwargs={} if profiles is None else {"format_profiles":profiles}
    r=qualify_windows_writer_review(
        args.primary,args.backup,args.raw_manifest,args.raw_samples_root,
        exiftool_candidate_id=args.exiftool_candidate,
        exiftool_cache_dir=args.exiftool_cache,
        exiftool_prepare_parent=args.exiftool_prepare,
        exiftool_workdir=args.exiftool_workdir,
        writer_workdir=args.writer_workdir,
        heic_fixture_cache=args.heic_fixture_cache,
        fetch_raw=args.fetch_raw,fetch_exiftool=args.fetch_exiftool,fetch_heic_fixture=args.fetch_heic_fixture,
        timeout_seconds=args.timeout,exiftool_subprocess_timeout_seconds=args.exiftool_timeout,
        **kwargs,
    )
    if args.report:write_windows_writer_qualification_report(args.report,r)
    _emit(r);return 0 if r.passed else 1

def cmd_format_write_matrix(args)->int:
    from .format_qualification import format_write_matrix
    _emit(format_write_matrix());return 0

def cmd_archive_format(args)->int:
    from .archive_format import inspect_archive_format, upgrade_archive_format
    if args.upgrade:
        try:r=upgrade_archive_format(args.output);_emit({'state':inspect_archive_format(args.output).to_dict(),'upgrade':r.to_dict()});return 0
        except Exception as e:_emit({'status':'blocked','error':str(e),'state':inspect_archive_format(args.output).to_dict()});return 2
    st=inspect_archive_format(args.output);_emit(st);return 0 if st.supported else 1

def cmd_product_init(args)->int:
    from .product import create_product_session
    s=create_product_session(args.workspace,args.input,args.output,require_apple_photos=args.require_apple_photos)
    _emit(s);return 0


def cmd_product_set(args)->int:
    from .product import update_product_session
    updates={}
    for name in ('source_vault','redundant_archive','redundant_source_vault','exit_evidence','real_takeout_qualification','windows_core_qualification','windows_writer_qualification','apple_photos_qualification'):
        value=getattr(args,name,None)
        if value is not None:updates[name]=str(value.resolve())
    if args.takeout_receipt is not None:updates['takeout_receipts']=[str(x.resolve()) for x in args.takeout_receipt]
    for name in ('all_takeout_parts_confirmed','redundant_archive_separate_media_confirmed','redundant_source_vault_separate_media_confirmed','require_apple_photos'):
        value=getattr(args,name,None)
        if value is not None:updates[name]=value
    if args.note is not None:updates['notes']=list(args.note)
    s=update_product_session(args.session,**updates);_emit(s);return 0


def cmd_product_status(args)->int:
    from .product import load_product_session,build_product_dashboard,write_product_dashboard
    s=load_product_session(args.session);d=build_product_dashboard(s)
    if args.report:write_product_dashboard(args.report,d)
    _emit(d);return 0 if d.retirement_state=='READY_FOR_FINAL_REVIEW' else 1


def cmd_qualify_real_takeout(args)->int:
    from .product import qualify_real_takeout
    q=qualify_real_takeout(args.input,args.output,args.report)
    _emit(q);return 0 if q.passed else 1


def cmd_prepare_real_world(args)->int:
    from .realworld import prepare_real_world_handoff
    r=prepare_real_world_handoff(args.session,args.output)
    _emit(r);return 0 if r.source_integrity_at_plan_time else 1


def cmd_host_diagnostics(args)->int:
    from .realworld import collect_host_diagnostics
    from .transactions import write_json_new
    r=collect_host_diagnostics(list(args.path or []))
    if args.report:write_json_new(args.report,r.to_dict())
    _emit(r);return 0 if r.passed_for_windows_handoff else 1


def cmd_privacy_support_bundle(args)->int:
    from .realworld import create_privacy_support_bundle
    r=create_privacy_support_bundle(args.session,list(args.report or []),args.output,include_source_hashes=args.include_source_hashes)
    _emit(r);return 0 if not r.get('rejected_reports') else 1


def cmd_desktop(args)->int:
    from .desktop import launch
    launch(args.session);return 0


def build_parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog='gpa-lab',description='Google Photos Takeout archive reconstruction research CLI')
    sub=p.add_subparsers(dest='command',required=True)
    def migration(name):
        q=sub.add_parser(name);q.add_argument('--input',nargs='+',required=True,type=Path);q.add_argument('--output',required=True,type=Path)
        q.add_argument('--decisions',type=Path);q.add_argument('--fs-type');q.add_argument('--reserve-fraction',type=float,default=0.05);return q
    q=migration('preview');q.add_argument('--include-review',action='store_true');q.set_defaults(func=cmd_preview)
    q=migration('run');q.add_argument('--run-id');q.set_defaults(func=cmd_run)
    q=sub.add_parser('audit');q.add_argument('--output',required=True,type=Path);q.set_defaults(func=cmd_audit)
    q=sub.add_parser('backfill-identity');q.add_argument('--output',required=True,type=Path);q.set_defaults(func=cmd_backfill_identity)
    q=sub.add_parser('vault');q.add_argument('--input',nargs='+',required=True,type=Path);q.add_argument('--vault',required=True,type=Path);q.set_defaults(func=cmd_vault)
    q=sub.add_parser('receipt');q.add_argument('--input',nargs='+',required=True,type=Path);q.add_argument('--output',required=True,type=Path);q.add_argument('--expected-count',required=True,type=int);q.add_argument('--export-id');q.add_argument('--confirmation-source',default='user_confirmed_from_takeout_ui_or_email');q.set_defaults(func=cmd_receipt)
    q=sub.add_parser('exit-evidence');q.add_argument('--output',required=True,type=Path);q.add_argument('--shared-albums-reviewed',action='store_true');q.add_argument('--wanted-shared-media-secured',action='store_true');q.add_argument('--partner-sharing-reviewed',action='store_true');q.add_argument('--locked-folder-reviewed',action='store_true');q.add_argument('--takeout-scope-reviewed',action='store_true');q.add_argument('--recent-changes-accounted-for',action='store_true');q.add_argument('--note',action='append');q.set_defaults(func=cmd_exit_evidence)
    q=sub.add_parser('admit-exiftool');q.add_argument('--candidate',required=True);q.add_argument('--archive',type=Path);q.add_argument('--cache',type=Path);q.add_argument('--prepare',type=Path);q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=120.0);q.set_defaults(func=cmd_admit_exiftool)
    q=sub.add_parser('qualify-exiftool');q.add_argument('--executable',required=True,type=Path);q.add_argument('--workdir',required=True,type=Path);q.add_argument('--distribution-root',type=Path);q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=30.0);q.set_defaults(func=cmd_qualify_exiftool)
    q=sub.add_parser('qualify-exiftool-format');q.add_argument('--executable',required=True,type=Path);q.add_argument('--workdir',required=True,type=Path);q.add_argument('--format-profile',required=True,choices=('jpeg-single-v1','png-single-v1','mov-single-v1','mp4-single-v1','m4v-single-v1','avif-single-v1','heic-single-v1'));q.add_argument('--distribution-root',type=Path);q.add_argument('--fixture',type=Path,help='exact pinned external fixture required for HEIC qualification');q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=30.0);q.set_defaults(func=cmd_qualify_exiftool_format)
    q=sub.add_parser('qualify-exiftool-relationship');q.add_argument('--executable',required=True,type=Path);q.add_argument('--workdir',required=True,type=Path);q.add_argument('--relationship-profile',required=True,choices=('motion-photo-composite-v1','live-photo-pair-v1'));q.add_argument('--format-profile',choices=('jpeg-single-v1','heic-single-v1','avif-single-v1'),default='jpeg-single-v1');q.add_argument('--fixture',type=Path);q.add_argument('--distribution-root',type=Path);q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=30.0);q.set_defaults(func=cmd_qualify_exiftool_relationship)
    q=sub.add_parser('bundle-exiftool-evidence');q.add_argument('--admission',required=True,type=Path);q.add_argument('--qualification',required=True,type=Path);q.add_argument('--archive',required=True,type=Path);q.add_argument('--distribution-root',required=True,type=Path);q.add_argument('--executable',required=True,type=Path);q.add_argument('--output',required=True,type=Path);q.set_defaults(func=cmd_bundle_exiftool_evidence)
    q=sub.add_parser('bundle-exiftool-format-evidence');q.add_argument('--base-evidence',required=True,type=Path);q.add_argument('--format-qualification',required=True,type=Path);q.add_argument('--archive',required=True,type=Path);q.add_argument('--distribution-root',required=True,type=Path);q.add_argument('--executable',required=True,type=Path);q.add_argument('--output',required=True,type=Path);q.set_defaults(func=cmd_bundle_exiftool_format_evidence)
    q=sub.add_parser('bundle-exiftool-relationship-evidence');q.add_argument('--format-evidence',required=True,type=Path);q.add_argument('--secondary-format-evidence',type=Path,help='required for pair relationships such as Live Photo');q.add_argument('--relationship-qualification',required=True,type=Path);q.add_argument('--distribution-root',required=True,type=Path);q.add_argument('--executable',required=True,type=Path);q.add_argument('--output',required=True,type=Path);q.set_defaults(func=cmd_bundle_exiftool_relationship_evidence)
    q=sub.add_parser('inspect-live-photo-pair');q.add_argument('--still',required=True,type=Path);q.add_argument('--movie',required=True,type=Path);q.add_argument('--timeout',type=float,default=30.0);q.set_defaults(func=cmd_inspect_live_photo_pair)
    q=sub.add_parser('qualify-raw-corpus');q.add_argument('--manifest',required=True,type=Path);q.add_argument('--samples-root',required=True,type=Path);q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=120.0);q.set_defaults(func=cmd_qualify_raw_corpus)
    q=sub.add_parser('fetch-raw-corpus');q.add_argument('--manifest',required=True,type=Path);q.add_argument('--samples-root',required=True,type=Path);q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=120.0);q.set_defaults(func=cmd_fetch_raw_corpus)
    q=sub.add_parser('qualify-windows-core');q.add_argument('--primary',required=True,type=Path);q.add_argument('--backup',required=True,type=Path);q.add_argument('--raw-manifest',required=True,type=Path);q.add_argument('--raw-samples-root',required=True,type=Path);q.add_argument('--fetch-raw',action='store_true');q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=120.0);q.set_defaults(func=cmd_qualify_windows_core)
    q=sub.add_parser('qualify-windows-release');q.add_argument('--primary',required=True,type=Path);q.add_argument('--backup',required=True,type=Path);q.add_argument('--raw-manifest',required=True,type=Path);q.add_argument('--raw-samples-root',required=True,type=Path);q.add_argument('--fetch-raw',action='store_true');q.add_argument('--exiftool-candidate',default='13.59-win-x64');q.add_argument('--exiftool-cache',required=True,type=Path);q.add_argument('--exiftool-prepare',required=True,type=Path);q.add_argument('--exiftool-workdir',required=True,type=Path);q.add_argument('--fetch-exiftool',action='store_true');q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=120.0);q.add_argument('--exiftool-timeout',type=float,default=30.0);q.set_defaults(func=cmd_qualify_windows_release)
    q=sub.add_parser('qualify-windows-writer');q.add_argument('--primary',required=True,type=Path);q.add_argument('--backup',required=True,type=Path);q.add_argument('--raw-manifest',required=True,type=Path);q.add_argument('--raw-samples-root',required=True,type=Path);q.add_argument('--fetch-raw',action='store_true');q.add_argument('--exiftool-candidate',default='13.59-win-x64');q.add_argument('--exiftool-cache',required=True,type=Path);q.add_argument('--exiftool-prepare',required=True,type=Path);q.add_argument('--exiftool-workdir',required=True,type=Path);q.add_argument('--writer-workdir',required=True,type=Path);q.add_argument('--heic-fixture-cache',required=True,type=Path);q.add_argument('--fetch-exiftool',action='store_true');q.add_argument('--fetch-heic-fixture',action='store_true');q.add_argument('--format-profile',action='append',choices=('jpeg-single-v1','png-single-v1','mov-single-v1','mp4-single-v1','m4v-single-v1','avif-single-v1','heic-single-v1'));q.add_argument('--report',type=Path);q.add_argument('--timeout',type=float,default=120.0);q.add_argument('--exiftool-timeout',type=float,default=30.0);q.set_defaults(func=cmd_qualify_windows_writer)
    q=sub.add_parser('format-write-matrix');q.set_defaults(func=cmd_format_write_matrix)
    q=sub.add_parser('archive-format');q.add_argument('--output',required=True,type=Path);q.add_argument('--upgrade',action='store_true');q.set_defaults(func=cmd_archive_format)
    q=sub.add_parser('verify');q.add_argument('--output',required=True,type=Path);q.add_argument('--source-vault',type=Path);q.add_argument('--source-vault-redundant-copy',type=Path);q.add_argument('--redundant-copy',type=Path);q.add_argument('--takeout-receipt',action='append',type=Path);q.add_argument('--exit-evidence',type=Path);q.add_argument('--all-parts-confirmed',action='store_true');q.add_argument('--redundant-copy-verified',action='store_true',help='manual compatibility flag; prefer --redundant-copy for hash verification');q.add_argument('--confirm-redundant-copy-separate-media',action='store_true',help='confirm the finished-archive redundant copy is on a different physical device');q.add_argument('--confirm-source-vault-copy-separate-media',action='store_true',help='confirm the raw-source-vault redundant copy is on a different physical device');q.set_defaults(func=cmd_verify)
    q=sub.add_parser('product-init');q.add_argument('--workspace',required=True,type=Path);q.add_argument('--input',nargs='+',required=True,type=Path);q.add_argument('--output',required=True,type=Path);q.add_argument('--require-apple-photos',action='store_true');q.set_defaults(func=cmd_product_init)
    q=sub.add_parser('product-set');q.add_argument('--session',required=True,type=Path);q.add_argument('--source-vault',type=Path);q.add_argument('--redundant-archive',type=Path);q.add_argument('--redundant-source-vault',type=Path);q.add_argument('--takeout-receipt',action='append',type=Path);q.add_argument('--exit-evidence',type=Path);q.add_argument('--real-takeout-qualification',type=Path);q.add_argument('--windows-core-qualification',type=Path);q.add_argument('--windows-writer-qualification',type=Path);q.add_argument('--apple-photos-qualification',type=Path);q.add_argument('--all-takeout-parts-confirmed',action=argparse.BooleanOptionalAction,default=None);q.add_argument('--redundant-archive-separate-media-confirmed',action=argparse.BooleanOptionalAction,default=None);q.add_argument('--redundant-source-vault-separate-media-confirmed',action=argparse.BooleanOptionalAction,default=None);q.add_argument('--require-apple-photos',action=argparse.BooleanOptionalAction,default=None);q.add_argument('--note',action='append');q.set_defaults(func=cmd_product_set)
    q=sub.add_parser('product-status');q.add_argument('--session',required=True,type=Path);q.add_argument('--report',type=Path);q.set_defaults(func=cmd_product_status)
    q=sub.add_parser('qualify-real-takeout');q.add_argument('--input',nargs='+',required=True,type=Path);q.add_argument('--output',required=True,type=Path);q.add_argument('--report',type=Path);q.set_defaults(func=cmd_qualify_real_takeout)
    q=sub.add_parser('prepare-real-world');q.add_argument('--session',required=True,type=Path);q.add_argument('--output',required=True,type=Path);q.set_defaults(func=cmd_prepare_real_world)
    q=sub.add_parser('host-diagnostics');q.add_argument('--path',action='append',type=Path);q.add_argument('--report',type=Path);q.set_defaults(func=cmd_host_diagnostics)
    q=sub.add_parser('privacy-support-bundle');q.add_argument('--session',required=True,type=Path);q.add_argument('--report',action='append',type=Path);q.add_argument('--output',required=True,type=Path);q.add_argument('--include-source-hashes',action='store_true');q.set_defaults(func=cmd_privacy_support_bundle)
    q=sub.add_parser('desktop');q.add_argument('--session',required=True,type=Path);q.set_defaults(func=cmd_desktop)
    return p


def main(argv=None)->int:
    args=build_parser().parse_args(argv)
    try:return int(args.func(args))
    except KeyboardInterrupt:return 130
    except Exception as e:
        _emit({'status':'error','error':str(e)});return 2

if __name__=='__main__':raise SystemExit(main())
