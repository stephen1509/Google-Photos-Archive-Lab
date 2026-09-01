#!/usr/bin/env python3
"""Mixed-media crash/fault recovery qualification for GPA.

This disposable lab tool exercises two interruption classes with real JPEG/MP4 media:
  A. process interruption between assets during the initial import (no run manifest),
  B. interruption after a corrected projection is fully written but before the
     current-revision pointer advances.

A normal rerun must converge to a clean archive without duplicate media/history loss.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

HERE=Path(__file__).resolve();sys.path.insert(0,str(HERE.parents[1]));sys.path.insert(0,str(HERE.parents[1]/'src'))

from gpa.archive_format import ensure_archive_for_write
from gpa.audit import audit_archive
from gpa.executor import RunExecutor
from gpa.incremental import IncrementalUpdater
from gpa.materializer import ArchiveMaterializer
from gpa.planner import LibraryPlanner
from gpa.revisions import RevisionStore
from tools.stress_mixed_incremental import _write_takeout

SCHEMA='gpa.stress-fault-recovery.v1'


def _actions(manifest:dict)->dict[str,int]:
    out={}
    for row in manifest.get('successes',[]):
        k=row.get('action','unknown');out[k]=out.get(k,0)+1
    return out


def _fail_after_projection():
    raise RuntimeError('simulated power loss after projection commit before current pointer')


def qualify(workdir:Path,photos:int,videos:int)->dict:
    workdir=Path(workdir);workdir.mkdir(parents=True,exist_ok=True)
    ffmpeg=shutil.which('ffmpeg');ffprobe=shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        return {'schema':SCHEMA,'passed':False,'checks':{'ffmpeg_and_ffprobe_available':False}}
    total=photos+videos
    root=workdir/'archive';initial=workdir/'takeout_initial.zip';corrected=workdir/'takeout_corrected.zip'
    media=_write_takeout(initial,photos,videos,datetime(2017,5,15,12,tzinfo=timezone.utc),'Fault initial',ffmpeg=ffmpeg)
    _write_takeout(corrected,photos,videos,datetime(2017,6,15,12,tzinfo=timezone.utc),'Fault corrected',ffmpeg=ffmpeg,media=media)

    # A. Simulate an abrupt process death after two assets were fully committed,
    # before the normal RunExecutor had a chance to emit its run manifest.
    ensure_archive_for_write(root)
    p1=LibraryPlanner([initial]);assets1=p1.plan()
    m1=ArchiveMaterializer(root,p1.lib)
    photo_asset=next(a for a in assets1 if (a.output_name or '').casefold().endswith('.jpg'))
    video_asset=next(a for a in assets1 if (a.output_name or '').casefold().endswith('.mp4'))
    m1.materialize(photo_asset);m1.materialize(video_asset)
    partial_audit=audit_archive(root)

    resumed=RunExecutor([initial],root,storage_reserve_fraction=0.0).execute(run_id='fault_resume_initial')
    resumed_actions=_actions(resumed);resume_audit=audit_archive(root)

    # B. Prepare corrected-date assets. Force one photo and one video to crash after
    # the new projection exists but before current metadata points at it.
    p2=LibraryPlanner([corrected]);assets2=p2.plan();m2=ArchiveMaterializer(root,p2.lib)
    up=IncrementalUpdater(root,m2);store=RevisionStore(root)
    crash_assets=[
        next(a for a in assets2 if (a.output_name or '').casefold().endswith('.jpg')),
        next(a for a in assets2 if (a.output_name or '').casefold().endswith('.mp4')),
    ]
    crash_details=[]
    for a in crash_assets:
        old=store.current_record(a.asset_id);old_rid=old.get('revision_id') if old else None
        try:
            up.apply(a,after_projection=_fail_after_projection)
            raised=False
        except RuntimeError as e:
            raised='simulated power loss' in str(e)
        after=store.current_record(a.asset_id);after_rid=after.get('revision_id') if after else None
        desired=up.inspect(a)
        crash_details.append({
            'asset_id':a.asset_id,'raised':raised,'pointer_unchanged':old_rid==after_rid,
            'desired_media_exists':desired.media.exists(),
            'old_media_gone':bool(desired.old_media and not desired.old_media.exists()),
            'kind':'video' if (a.output_name or '').casefold().endswith('.mp4') else 'photo',
        })

    healed=RunExecutor([corrected],root,storage_reserve_fraction=0.0).execute(run_id='fault_heal_corrected')
    healed_actions=_actions(healed);final_audit=audit_archive(root)

    checks={
        'ffmpeg_and_ffprobe_available':True,
        'partial_two_assets_auditable':partial_audit.ok and partial_audit.assets_checked==2 and partial_audit.media_validation_passed==2,
        'resume_complete':resumed.get('status')=='complete',
        'resume_recognizes_committed_assets':resumed_actions.get('unchanged')==2,
        'resume_imports_remaining_assets':resumed_actions.get('new')==total-2,
        'resume_audit_clean':resume_audit.ok and resume_audit.assets_checked==total and resume_audit.revisions_checked==total,
        'photo_projection_crash_reproduced':any(x['kind']=='photo' and x['raised'] and x['pointer_unchanged'] and x['desired_media_exists'] and x['old_media_gone'] for x in crash_details),
        'video_projection_crash_reproduced':any(x['kind']=='video' and x['raised'] and x['pointer_unchanged'] and x['desired_media_exists'] and x['old_media_gone'] for x in crash_details),
        'healed_complete':healed.get('status')=='complete',
        'healed_all_relocated':healed_actions=={'relocated':total},
        'final_two_revisions_each':final_audit.revisions_checked==total*2,
        'validation_preserved':final_audit.media_validation_passed==total,
        'final_audit_clean':final_audit.ok,
    }
    return {
        'schema':SCHEMA,'photos':photos,'videos':videos,'total':total,'passed':all(checks.values()),'checks':checks,
        'resume_actions':resumed_actions,'healed_actions':healed_actions,'crash_details':crash_details,
        'audit':{'assets':final_audit.assets_checked,'revisions':final_audit.revisions_checked,'validation_passed':final_audit.media_validation_passed,'problems':[vars(x) for x in final_audit.problems]},
    }


def main(argv=None)->int:
    ap=argparse.ArgumentParser();ap.add_argument('--workdir',required=True,type=Path);ap.add_argument('--photos',type=int,default=8);ap.add_argument('--videos',type=int,default=2);ap.add_argument('--report',type=Path);ap.add_argument('--force',action='store_true')
    a=ap.parse_args(argv)
    if a.photos<1 or a.videos<1:ap.error('--photos and --videos must both be >= 1')
    if a.workdir.exists() and any(a.workdir.iterdir()):
        if not a.force:ap.error('workdir is not empty; use --force only for a disposable lab directory')
        shutil.rmtree(a.workdir)
    result=qualify(a.workdir,a.photos,a.videos);text=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True)
        if a.report.exists():raise FileExistsError(a.report)
        a.report.write_text(text,encoding='utf-8')
    print(text,end='');return 0 if result.get('passed') else 1

if __name__=='__main__':raise SystemExit(main())
