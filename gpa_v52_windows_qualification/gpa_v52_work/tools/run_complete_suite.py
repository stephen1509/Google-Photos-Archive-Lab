#!/usr/bin/env python3
"""Run the complete GPA automated suite in bounded isolated pytest modules.

Each module runs in a fresh Python process with a hard timeout. Optional bounded
parallelism reduces release-gate wall-clock time without sharing pytest process state.
JUnit XML is aggregated into one machine-readable `gpa.test-suite.v1` report.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SCHEMA='gpa.test-suite.v1'
MODULES=(
    'tests/test_archive_format.py',
    'tests/test_backup_recovery.py',
    'tests/test_core.py',
    'tests/test_datapacks.py',
    'tests/test_exiftool_distribution.py',
    'tests/test_exiftool_evidence.py',
    'tests/test_format_qualification.py',
    'tests/test_format_evidence.py',
    'tests/test_format_roundtrip_qualification.py',
    'tests/test_relationship_roundtrip_qualification.py',
    'tests/test_relationship_evidence.py',
    'tests/test_heif_avif_qualification.py',
    'tests/test_exiftool_qualification.py',
    'tests/test_hardening.py',
    'tests/test_identity_observations.py',
    'tests/test_live_photo_integrity.py',
    'tests/test_media_validation.py',
    'tests/test_product_workflow.py',
    'tests/test_product_integration.py',
    'tests/test_realworld_handoff.py',
    'tests/test_realworld_integration.py',
    'tests/test_raw_validation.py',
    'tests/test_raw_corpus.py',
    'tests/test_raw_corpus_fetch.py',
    'tests/test_qualification_fixtures_acquire.py',
    'tests/test_windows_qualification.py',
    'tests/test_windows_release_qualification.py',
    'tests/test_windows_writer_qualification.py',
    'tests/test_windows_storage.py',
)


def _counts(xml_path:Path)->dict[str,int|float]:
    root=ET.parse(xml_path).getroot()
    suites=[root] if root.tag=='testsuite' else list(root.findall('testsuite'))
    out={'tests':0,'failures':0,'errors':0,'skipped':0,'time':0.0}
    for s in suites:
        for k in ('tests','failures','errors','skipped'):out[k]+=int(s.attrib.get(k,0))
        out['time']+=float(s.attrib.get('time',0.0))
    return out


def _run_one(repo_root:Path,td:Path,i:int,module:str,timeout_seconds:float)->tuple[int,dict]:
    xml=td/f'{i:02d}.xml';row={'module':module,'timed_out':False,'returncode':None}
    env=os.environ.copy();env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    try:
        cp=subprocess.run(
            [sys.executable,'-m','pytest','-q',module,f'--junitxml={xml}'],
            cwd=repo_root,capture_output=True,text=True,timeout=timeout_seconds,check=False,env=env,
        )
        row['returncode']=cp.returncode
        row['stdout_tail']='\n'.join(cp.stdout.splitlines()[-8:])
        row['stderr_tail']='\n'.join(cp.stderr.splitlines()[-8:])
    except subprocess.TimeoutExpired as e:
        row['timed_out']=True;row['error']=f'timed out after {timeout_seconds:g}s'
        stdout=(e.stdout or b'').decode(errors='replace') if isinstance(e.stdout,bytes) else (e.stdout or '')
        stderr=(e.stderr or b'').decode(errors='replace') if isinstance(e.stderr,bytes) else (e.stderr or '')
        row['stdout_tail']='\n'.join(stdout.splitlines()[-8:]);row['stderr_tail']='\n'.join(stderr.splitlines()[-8:])
    if xml.exists():row.update(_counts(xml))
    else:row.update({'tests':0,'failures':0,'errors':0,'skipped':0,'time':0.0})
    row['passed']=(not row['timed_out'] and row['returncode']==0 and row['failures']==0 and row['errors']==0)
    return i,row


def run_suite(repo_root:Path,*,timeout_seconds:float=120.0,jobs:int=1)->dict:
    repo_root=Path(repo_root).resolve()
    if timeout_seconds<=0:raise ValueError('timeout_seconds must be > 0')
    if jobs<1:raise ValueError('jobs must be >= 1')
    workers=min(int(jobs),len(MODULES));rows_by_index={}
    with tempfile.TemporaryDirectory(prefix='gpa-complete-suite-') as rawtd:
        td=Path(rawtd)
        if workers==1:
            for i,module in enumerate(MODULES):
                idx,row=_run_one(repo_root,td,i,module,timeout_seconds);rows_by_index[idx]=row
        else:
            with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='gpa-suite') as ex:
                futures=[ex.submit(_run_one,repo_root,td,i,module,timeout_seconds) for i,module in enumerate(MODULES)]
                for fut in as_completed(futures):
                    idx,row=fut.result();rows_by_index[idx]=row
    rows=[rows_by_index[i] for i in range(len(MODULES))]
    tot={'tests':0,'failures':0,'errors':0,'skipped':0,'time':0.0};passed=True
    for row in rows:
        for k in ('tests','failures','errors','skipped'):tot[k]+=int(row[k])
        tot['time']+=float(row['time']);passed=passed and bool(row['passed'])
    return {
        'schema':SCHEMA,'created_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version.split()[0],
        'timeout_seconds_per_module':timeout_seconds,'jobs':workers,'modules':rows,'totals':tot,'passed':passed,
    }


def main(argv=None)->int:
    ap=argparse.ArgumentParser();ap.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--timeout',type=float,default=120.0);ap.add_argument('--jobs',type=int,default=1);ap.add_argument('--report',type=Path)
    a=ap.parse_args(argv);result=run_suite(a.repo_root,timeout_seconds=a.timeout,jobs=a.jobs);text=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True)
        if a.report.exists():raise FileExistsError(a.report)
        a.report.write_text(text,encoding='utf-8')
    print(text,end='');return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
