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
SPLIT_MODULES={
    # Measured on Windows/Python 3.12: these files exceed the 120s module cap
    # despite continuing to make progress.  Split collected node ids instead
    # of weakening the bound or dropping coverage.
    'tests/test_hardening.py':50,
    'tests/test_media_validation.py':10,
}


def _counts(xml_path:Path)->dict[str,int|float]:
    root=ET.parse(xml_path).getroot()
    suites=[root] if root.tag=='testsuite' else list(root.findall('testsuite'))
    out={'tests':0,'failures':0,'errors':0,'skipped':0,'time':0.0}
    for s in suites:
        for k in ('tests','failures','errors','skipped'):out[k]+=int(s.attrib.get(k,0))
        out['time']+=float(s.attrib.get('time',0.0))
    return out


def _split_targets(repo_root:Path,module:str)->list[tuple[str,tuple[str,...]]]:
    size=SPLIT_MODULES.get(module)
    if not size:return [(module,(module,))]
    cp=subprocess.run([sys.executable,'-m','pytest','--collect-only','-q',module],cwd=repo_root,capture_output=True,text=True,check=False)
    nodeids=[line.strip() for line in cp.stdout.splitlines() if line.startswith(module+'::')]
    if cp.returncode or not nodeids:
        raise RuntimeError(f'could not collect bounded targets for {module}: {cp.stderr.strip() or cp.stdout.strip()}')
    groups=[nodeids[n:n+size] for n in range(0,len(nodeids),size)]
    return [(f'{module} [{n+1}/{len(groups)}]',tuple(group)) for n,group in enumerate(groups)]


def _run_one(repo_root:Path,td:Path,i:int,target:tuple[str,tuple[str,...]],timeout_seconds:float)->tuple[int,dict]:
    """Run one module with its own pytest base, system temp, and child tree.

    Windows keeps inherited handles open longer than POSIX.  Reusing a parent
    ``--basetemp`` (especially through PYTEST_ADDOPTS) allowed a timed-out
    module to contaminate a later module.  Each invocation therefore owns all
    of its temporary paths and a timeout terminates the whole process tree.
    """
    module,arguments=target
    xml=td/f'{i:02d}.xml';row={'module':module,'timed_out':False,'returncode':None}
    module_temp=td/f'module-{i:02d}';pytest_base=td/f'pytest-{i:02d}'
    module_temp.mkdir();pytest_base.mkdir()
    env=os.environ.copy();env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    env.pop('PYTEST_ADDOPTS',None)
    env['TEMP']=str(module_temp);env['TMP']=str(module_temp)
    env['PYTHONDONTWRITEBYTECODE']='1'
    command=[sys.executable,'-m','pytest','-q',*arguments,'-p','no:cacheprovider',f'--basetemp={pytest_base}',f'--junitxml={xml}']
    creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0) if os.name=='nt' else 0
    process=subprocess.Popen(command,cwd=repo_root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,creationflags=creationflags)
    try:
        stdout,stderr=process.communicate(timeout=timeout_seconds)
        row['returncode']=process.returncode
        row['stdout_tail']='\n'.join(stdout.splitlines()[-8:])
        row['stderr_tail']='\n'.join(stderr.splitlines()[-8:])
    except subprocess.TimeoutExpired:
        row['timed_out']=True;row['error']=f'timed out after {timeout_seconds:g}s'
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,text=True,check=False)
        else:
            process.kill()
        stdout,stderr=process.communicate()
        row['stdout_tail']='\n'.join(stdout.splitlines()[-8:]);row['stderr_tail']='\n'.join(stderr.splitlines()[-8:])
    if xml.exists():row.update(_counts(xml))
    else:row.update({'tests':0,'failures':0,'errors':0,'skipped':0,'time':0.0})
    row['passed']=(not row['timed_out'] and row['returncode']==0 and row['failures']==0 and row['errors']==0)
    return i,row


def run_suite(repo_root:Path,*,timeout_seconds:float=120.0,jobs:int=1)->dict:
    repo_root=Path(repo_root).resolve()
    if timeout_seconds<=0:raise ValueError('timeout_seconds must be > 0')
    if jobs<1:raise ValueError('jobs must be >= 1')
    targets=[target for module in MODULES for target in _split_targets(repo_root,module)]
    workers=min(int(jobs),len(targets));rows_by_index={}
    # Do not delete this directory before the report is returned.  On Windows a
    # just-terminated subprocess can retain a handle briefly; eager rmtree then
    # blocks the release gate after tests have finished.  The caller supplies a
    # disposable parent TEMP/TMP path and owns its later cleanup.
    td=Path(tempfile.mkdtemp(prefix='gpa-complete-suite-'))
    if workers==1:
        for i,target in enumerate(targets):
            idx,row=_run_one(repo_root,td,i,target,timeout_seconds);rows_by_index[idx]=row
    else:
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='gpa-suite') as ex:
            futures=[ex.submit(_run_one,repo_root,td,i,target,timeout_seconds) for i,target in enumerate(targets)]
            for fut in as_completed(futures):
                idx,row=fut.result();rows_by_index[idx]=row
    rows=[rows_by_index[i] for i in range(len(targets))]
    tot={'tests':0,'failures':0,'errors':0,'skipped':0,'time':0.0};passed=True
    for row in rows:
        for k in ('tests','failures','errors','skipped'):tot[k]+=int(row[k])
        tot['time']+=float(row['time']);passed=passed and bool(row['passed'])
    return {
        'schema':SCHEMA,'created_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version.split()[0],
        'timeout_seconds_per_module':timeout_seconds,'jobs':workers,'temporary_root':str(td),'modules':rows,'totals':tot,'passed':passed,
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
