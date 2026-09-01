#!/usr/bin/env python3
"""Reproducible real-JPEG incremental/relocation stress qualification.

Creates a disposable synthetic Takeout made of genuinely decodable, unique JPEGs,
then runs three archive passes:
  1. initial import,
  2. identical re-import (must be unchanged/idempotent),
  3. same media bytes with corrected dates (must relocate without losing validation).

This is a lab qualification tool.  It never reads a user's archive.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import shutil
import sys
import time
import zipfile

HERE=Path(__file__).resolve()
sys.path.insert(0,str(HERE.parents[1]/'src'))

from PIL import Image, __version__ as PILLOW_VERSION
from gpa.audit import audit_archive
from gpa.executor import RunExecutor

SCHEMA='gpa.stress-jpeg-incremental.v1'


def _jpeg(i:int)->bytes:
    # Width changes above 255 so the deterministic color cycle cannot create
    # byte-identical repeats in larger runs.
    w=48+(i//256);h=32
    im=Image.new('RGB',(w,h));px=im.load()
    for y in range(h):
        for x in range(w):
            px[x,y]=((x*7+i)%256,(y*11+i*3)%256,(x+y+i*13)%256)
    b=io.BytesIO();im.save(b,'JPEG',quality=88,subsampling=0);return b.getvalue()


def _write_takeout(path:Path,count:int,start:datetime,description_prefix:str,media:dict[str,bytes]|None=None)->dict[str,bytes]:
    rows={} if media is None else dict(media)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as z:
        for i in range(count):
            name=f'IMG_{i:06d}.jpg';logical=f'Photos from 2017/{name}'
            raw=rows.setdefault(logical,_jpeg(i));z.writestr(logical,raw)
            ts=int((start+timedelta(minutes=i)).timestamp())
            side={'title':name,'photoTakenTime':{'timestamp':str(ts)},'description':f'{description_prefix} {i}'}
            z.writestr(logical+'.json',json.dumps(side,ensure_ascii=False))
    return rows


def _actions(manifest:dict)->dict[str,int]:
    out={}
    for row in manifest.get('successes',[]):
        k=row.get('action','unknown');out[k]=out.get(k,0)+1
    return out


def _run(ex:RunExecutor,run_id:str)->tuple[dict,float]:
    t=time.perf_counter();m=ex.execute(run_id=run_id);return m,time.perf_counter()-t


def qualify(workdir:Path,count:int)->dict:
    workdir=Path(workdir);workdir.mkdir(parents=True,exist_ok=True)
    root=workdir/'archive';initial=workdir/'takeout_initial.zip';corrected=workdir/'takeout_corrected.zip'
    media=_write_takeout(initial,count,datetime(2017,5,15,12,tzinfo=timezone.utc),'Initial stress photo')
    _write_takeout(corrected,count,datetime(2017,6,15,12,tzinfo=timezone.utc),'Corrected stress photo',media)

    m1,t1=_run(RunExecutor([initial],root,storage_reserve_fraction=0.0),'stress_initial')
    a1=audit_archive(root)
    m2,t2=_run(RunExecutor([initial],root,storage_reserve_fraction=0.0),'stress_reimport')
    a2=audit_archive(root)
    m3,t3=_run(RunExecutor([corrected],root,storage_reserve_fraction=0.0),'stress_corrected')
    a3=audit_archive(root)

    actions1=_actions(m1);actions2=_actions(m2);actions3=_actions(m3)
    checks={
        'initial_complete':m1.get('status')=='complete',
        'initial_all_new':actions1=={'new':count},
        'initial_audit_clean':a1.ok and a1.assets_checked==count and a1.media_validation_passed==count,
        'reimport_complete':m2.get('status')=='complete',
        'reimport_all_unchanged':actions2=={'unchanged':count},
        'reimport_no_new_revisions':a2.revisions_checked==count,
        'reimport_audit_clean':a2.ok and a2.media_validation_passed==count,
        'correction_complete':m3.get('status')=='complete',
        'correction_all_relocated':actions3=={'relocated':count},
        'correction_two_revisions_each':a3.revisions_checked==count*2,
        'correction_validation_preserved':a3.media_validation_passed==count,
        'final_audit_clean':a3.ok,
    }
    return {
        'schema':SCHEMA,'count':count,'passed':all(checks.values()),'checks':checks,
        'actions':{'initial':actions1,'reimport':actions2,'corrected':actions3},
        'seconds':{'initial':round(t1,3),'reimport':round(t2,3),'corrected':round(t3,3)},
        'audit':{'assets':a3.assets_checked,'revisions':a3.revisions_checked,'validation_passed':a3.media_validation_passed,'problems':[vars(x) for x in a3.problems]},
        'environment':{'python':sys.version.split()[0],'pillow':PILLOW_VERSION},
        'artifacts':{'initial_zip_bytes':initial.stat().st_size,'corrected_zip_bytes':corrected.stat().st_size},
    }


def main(argv=None)->int:
    ap=argparse.ArgumentParser();ap.add_argument('--workdir',required=True,type=Path);ap.add_argument('--count',type=int,default=500);ap.add_argument('--report',type=Path);ap.add_argument('--force',action='store_true')
    a=ap.parse_args(argv)
    if a.count<1:ap.error('--count must be >= 1')
    if a.workdir.exists() and any(a.workdir.iterdir()):
        if not a.force:ap.error('workdir is not empty; use --force only for a disposable lab directory')
        shutil.rmtree(a.workdir)
    result=qualify(a.workdir,a.count)
    text=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True)
        if a.report.exists():raise FileExistsError(a.report)
        a.report.write_text(text,encoding='utf-8')
    print(text,end='');return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
