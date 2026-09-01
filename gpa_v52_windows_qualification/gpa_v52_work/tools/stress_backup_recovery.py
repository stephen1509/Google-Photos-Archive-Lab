#!/usr/bin/env python3
"""Source-vault and verified-mirror interruption/recovery qualification.

Uses only a disposable work directory. It proves that:
  * a raw Takeout ZIP committed to the Source Vault before provenance recording is
    recovered on rerun without changing or duplicating the original bytes;
  * interrupted Source Vault and finished-archive mirrors resume by reusing exact
    already-committed files;
  * final mirrors hash-verify against frozen tree manifests;
  * the mirrored Source Vault independently passes its vault audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

HERE=Path(__file__).resolve();sys.path.insert(0,str(HERE.parents[1]/'src'))

from gpa.mirror import build_tree_manifest,materialize_verified_mirror,verify_mirror
from gpa.source_vault import audit_source_vault,vault_source_archive,vault_source_archives
from gpa.transactions import sha256_file

SCHEMA='gpa.stress-backup-recovery.v1'


def _takeout(path:Path,i:int)->None:
    media=(f'GPA-RAW-TAKEOUT-FIXTURE-{i:06d}-'.encode()+hashlib.sha256(str(i).encode()).digest())*5
    side={'title':f'IMG_{i:06d}.jpg','photoTakenTime':{'timestamp':str(1494806400+i)},'description':f'backup recovery fixture {i}'}
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as z:
        z.writestr(f'Google Photos/Photos from 2017/IMG_{i:06d}.jpg',media)
        z.writestr(f'Google Photos/Photos from 2017/IMG_{i:06d}.jpg.json',json.dumps(side,sort_keys=True))


def _make_archive_tree(root:Path,count:int)->None:
    for i in range(count):
        if i%4==0:rel=Path('metadata')/'assets'/f'{i:06d}.json'
        elif i%4==1:rel=Path('metadata')/'portable'/f'{i:06d}.xmp'
        elif i%4==2:rel=Path('Photos')/'2017'/'05 - May'/f'IMG_{i:06d}.jpg'
        else:rel=Path('_Needs Placement')/'Date'/f'UNKNOWN_{i:06d}.bin'
        p=root/rel;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes((f'GPA-ARCHIVE-FIXTURE-{i:06d}-'.encode()+hashlib.sha256(f'archive-{i}'.encode()).digest())*3)


def _crash_after(limit:int,message:str):
    def cb(n:int,path:Path)->None:
        if n==limit:raise RuntimeError(message)
    return cb


def qualify(workdir:Path,source_count:int,archive_files:int)->dict:
    workdir=Path(workdir);workdir.mkdir(parents=True,exist_ok=True)
    rawdir=workdir/'raw';rawdir.mkdir();sources=[]
    for i in range(source_count):
        p=rawdir/f'takeout-20260822T000000Z-{i+1:03d}.zip';_takeout(p,i);sources.append(p)
    original={p.name:sha256_file(p) for p in sources}

    vault=workdir/'source-vault';vault_crash=False
    def vault_fail(dest:Path)->None:
        nonlocal vault_crash
        vault_crash=True
        raise RuntimeError('simulated source-vault interruption after byte commit')
    try:vault_source_archive(sources[0],vault,after_bytes_commit=vault_fail)
    except RuntimeError as e:
        if 'simulated source-vault interruption' not in str(e):raise
    healed=vault_source_archives(sources,vault)
    vault_audit=audit_source_vault(vault)

    vault_manifest=build_tree_manifest(vault)
    vault_mirror=workdir/'source-vault-mirror';vault_mirror_crash=False
    vault_limit=max(1,min(3,len(vault_manifest['files'])))
    try:materialize_verified_mirror(vault_manifest,vault,vault_mirror,after_commit=_crash_after(vault_limit,'simulated source-vault mirror interruption'))
    except RuntimeError as e:
        if 'simulated source-vault mirror interruption' not in str(e):raise
        vault_mirror_crash=True
    vault_resume=materialize_verified_mirror(vault_manifest,vault,vault_mirror)
    vault_mirror_verify=verify_mirror(vault_manifest,vault_mirror,report_extras=True)
    mirrored_vault_audit=audit_source_vault(vault_mirror)

    archive=workdir/'finished-archive';archive.mkdir();_make_archive_tree(archive,archive_files)
    archive_manifest=build_tree_manifest(archive)
    archive_mirror=workdir/'finished-archive-mirror';archive_mirror_crash=False
    archive_limit=max(1,min(5,len(archive_manifest['files'])))
    try:materialize_verified_mirror(archive_manifest,archive,archive_mirror,after_commit=_crash_after(archive_limit,'simulated archive mirror interruption'))
    except RuntimeError as e:
        if 'simulated archive mirror interruption' not in str(e):raise
        archive_mirror_crash=True
    archive_resume=materialize_verified_mirror(archive_manifest,archive,archive_mirror)
    archive_mirror_verify=verify_mirror(archive_manifest,archive_mirror,report_extras=True)

    originals_after={p.name:sha256_file(p) for p in sources}
    stage_files=[p.as_posix() for p in workdir.rglob('.gpa-stage-*')]
    checks={
        'source_vault_fault_reproduced':vault_crash,
        'source_vault_rerun_reused_crashed_bytes':bool(healed and healed[0].reused_existing_bytes),
        'source_vault_audit_clean':vault_audit.ok and vault_audit.records_checked==source_count and vault_audit.archives_checked==source_count,
        'raw_takeout_originals_unchanged':original==originals_after,
        'source_vault_mirror_fault_reproduced':vault_mirror_crash,
        'source_vault_mirror_resumed':vault_resume.ok and vault_resume.files_reused>=vault_limit,
        'source_vault_mirror_verified':vault_mirror_verify.ok and not vault_mirror_verify.extra,
        'mirrored_source_vault_audit_clean':mirrored_vault_audit.ok and mirrored_vault_audit.records_checked==source_count,
        'archive_mirror_fault_reproduced':archive_mirror_crash,
        'archive_mirror_resumed':archive_resume.ok and archive_resume.files_reused>=archive_limit,
        'archive_mirror_verified':archive_mirror_verify.ok and not archive_mirror_verify.extra,
        'no_staging_files_left':not stage_files,
    }
    return {
        'schema':SCHEMA,'source_count':source_count,'archive_files':archive_files,'passed':all(checks.values()),'checks':checks,
        'source_vault':{'records':vault_audit.records_checked,'archives':vault_audit.archives_checked,'problems':vault_audit.problems},
        'source_vault_mirror':{'files':len(vault_manifest['files']),'resume_copied':vault_resume.files_copied,'resume_reused':vault_resume.files_reused,'problems':vault_mirror_verify.missing+vault_mirror_verify.mismatched},
        'archive_mirror':{'files':len(archive_manifest['files']),'resume_copied':archive_resume.files_copied,'resume_reused':archive_resume.files_reused,'problems':archive_mirror_verify.missing+archive_mirror_verify.mismatched},
        'staging_files':stage_files,
    }


def main(argv=None)->int:
    ap=argparse.ArgumentParser();ap.add_argument('--workdir',required=True,type=Path);ap.add_argument('--sources',type=int,default=12);ap.add_argument('--archive-files',type=int,default=40);ap.add_argument('--report',type=Path);ap.add_argument('--force',action='store_true')
    a=ap.parse_args(argv)
    if a.sources<1 or a.archive_files<1:ap.error('--sources and --archive-files must both be >= 1')
    if a.workdir.exists() and any(a.workdir.iterdir()):
        if not a.force:ap.error('workdir is not empty; use --force only for a disposable lab directory')
        shutil.rmtree(a.workdir)
    result=qualify(a.workdir,a.sources,a.archive_files);text=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True)
        if a.report.exists():raise FileExistsError(a.report)
        a.report.write_text(text,encoding='utf-8')
    print(text,end='');return 0 if result.get('passed') else 1

if __name__=='__main__':raise SystemExit(main())
