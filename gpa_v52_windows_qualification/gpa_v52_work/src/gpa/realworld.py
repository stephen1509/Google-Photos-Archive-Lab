from __future__ import annotations

"""Real-world qualification handoff helpers.

This module prepares evidence collection for a genuine Windows machine without
claiming that this non-Windows build has produced Windows evidence.  Generated
handoff files are inert until the operator runs them on the target Windows host.
Source Takeout archives are never copied into diagnostic/support bundles.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import platform
import shutil
import socket
import sys
import zipfile

from .product import ProductSession, load_product_session, verify_session_sources
from .transactions import write_json_new

PLAN_SCHEMA = 'gpa.real-world-qualification-plan.v1'
DIAGNOSTICS_SCHEMA = 'gpa.host-diagnostics.v1'
SUPPORT_SCHEMA = 'gpa.privacy-support-bundle.v1'


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _source_binding(session: ProductSession, *, include_hashes: bool = True) -> list[dict[str, Any]]:
    rows=[]
    for i,s in enumerate(session.sources,1):
        row={'source_index':i,'size':s.size,'zip_members':s.zip_members}
        if include_hashes:row['sha256']=s.sha256
        rows.append(row)
    return rows


@dataclass
class RealWorldQualificationPlan:
    schema: str = PLAN_SCHEMA
    created_utc: str = ''
    session_id: str = ''
    source_binding: list[dict[str, Any]] = field(default_factory=list)
    source_integrity_at_plan_time: bool = False
    source_integrity_problems: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    safety_invariants: dict[str, bool] = field(default_factory=dict)
    generated_files: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    windows_evidence_collected: bool = False
    production_write_approved: bool = False
    google_retirement_approved: bool = False

    def to_dict(self)->dict[str,Any]: return asdict(self)


@dataclass
class HostDiagnostics:
    schema: str = DIAGNOSTICS_SCHEMA
    created_utc: str = ''
    platform: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    paths: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    passed_for_windows_handoff: bool = False
    production_write_approved: bool = False
    google_retirement_approved: bool = False

    def to_dict(self)->dict[str,Any]: return asdict(self)


def collect_host_diagnostics(paths: list[Path] | None = None) -> HostDiagnostics:
    """Collect read-only host/path diagnostics.  Never treats non-Windows as qualified."""
    paths=[Path(p) for p in (paths or [])]
    d=HostDiagnostics(created_utc=_utcnow())
    d.platform={
        'system':platform.system(),'release':platform.release(),'version':platform.version(),
        'machine':platform.machine(),'processor':platform.processor(),
        'hostname_sha256':hashlib.sha256(socket.gethostname().encode('utf-8','replace')).hexdigest(),
    }
    d.runtime={
        'python_version':platform.python_version(),'python_implementation':platform.python_implementation(),
        'pointer_bits':64 if sys.maxsize > 2**32 else 32,
        'executable_sha256':_sha256(Path(sys.executable)) if Path(sys.executable).is_file() else None,
    }
    for i,p in enumerate(paths,1):
        row={'path_index':i,'exists':False,'is_dir':False,'free_bytes':None,'total_bytes':None}
        try:
            row['exists']=p.exists();row['is_dir']=p.is_dir()
            probe=p if p.exists() else p.parent
            usage=shutil.disk_usage(probe)
            row['free_bytes']=usage.free;row['total_bytes']=usage.total
        except OSError as e:
            row['error']=f'{type(e).__name__}: {e}'
        d.paths.append(row)
    windows=platform.system()=='Windows'
    x64=d.runtime['pointer_bits']==64 and platform.machine().casefold() in {'amd64','x86_64'}
    d.checks={
        'platform_is_windows':windows,
        'runtime_is_64_bit':d.runtime['pointer_bits']==64,
        'windows_x64_eligible':bool(windows and x64),
        'python_executable_fingerprinted':isinstance(d.runtime.get('executable_sha256'),str) and len(d.runtime['executable_sha256'])==64,
        'requested_paths_accessible':all(bool(x.get('exists')) for x in d.paths) if d.paths else True,
    }
    if not windows:d.warnings.append('This diagnostic run is not genuine Windows evidence.')
    d.passed_for_windows_handoff=all(d.checks.values())
    return d


_POWERSHELL = r'''param(
  [Parameter(Mandatory=$true)][string]$Python,
  [Parameter(Mandatory=$true)][string]$Session,
  [Parameter(Mandatory=$true)][string]$Primary,
  [Parameter(Mandatory=$true)][string]$Backup,
  [Parameter(Mandatory=$true)][string]$RawManifest,
  [Parameter(Mandatory=$true)][string]$RawSamples,
  [Parameter(Mandatory=$true)][string]$EvidenceDir,
  [string]$ExifToolCache = "",
  [string]$ExifToolPrepare = "",
  [string]$ExifToolWork = "",
  [string]$WriterWork = "",
  [string]$HeicFixtureCache = ""
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null

& $Python -m gpa product-status --session $Session --report (Join-Path $EvidenceDir "PRODUCT_STATUS_BEFORE.json")
if ($LASTEXITCODE -notin 0,1) { throw "product-status failed" }

& $Python -m gpa host-diagnostics --path $Primary --path $Backup --report (Join-Path $EvidenceDir "HOST_DIAGNOSTICS.json")
if ($LASTEXITCODE -ne 0) { throw "host diagnostics did not qualify as Windows x64" }

& $Python -m gpa qualify-windows-core --primary $Primary --backup $Backup --raw-manifest $RawManifest --raw-samples-root $RawSamples --report (Join-Path $EvidenceDir "WINDOWS_CORE.json")
if ($LASTEXITCODE -ne 0) { throw "Windows core qualification failed" }

if ($ExifToolCache -and $ExifToolPrepare -and $ExifToolWork -and $WriterWork -and $HeicFixtureCache) {
  & $Python -m gpa qualify-windows-writer --primary $Primary --backup $Backup --raw-manifest $RawManifest --raw-samples-root $RawSamples --exiftool-cache $ExifToolCache --exiftool-prepare $ExifToolPrepare --exiftool-workdir $ExifToolWork --writer-workdir $WriterWork --heic-fixture-cache $HeicFixtureCache --report (Join-Path $EvidenceDir "WINDOWS_WRITER.json")
  if ($LASTEXITCODE -ne 0) { throw "Windows writer evidence qualification failed" }
}

Write-Host "Evidence collection complete. This does NOT authorize production metadata writing or Google deletion."
'''

_README = '''Google Photos Archive Lab - Windows / Real Takeout qualification handoff

SAFETY
- Keep Google Takeout ZIPs unchanged. The handoff does not require extracting or rewriting them.
- Windows qualification reports are evidence only. They do not enable metadata writing.
- Do not delete Google originals based on these reports alone.
- Use physically independent primary and backup storage for the storage-independence gate.

WORKFLOW
1. Copy/extract this GPA checkpoint onto the target Windows x64 PC.
2. Keep the session and Takeout ZIPs accessible at their original byte content.
3. Run real-Takeout qualification from GPA against the exact session sources.
4. Run RUN_WINDOWS_QUALIFICATION.ps1 with explicit paths for Python, session, primary/backup storage, RAW manifest/samples and evidence directory.
5. Optionally provide ExifTool/HEIC qualification paths to collect writer REVIEW evidence.
6. Generate a privacy support bundle if troubleshooting is needed. It excludes Takeout bytes and source filenames/paths by design.
7. Attach generated evidence to the product session and recompute product-status.

A successful Windows run still leaves other retirement gates fail-closed until their independent evidence exists.
'''


def prepare_real_world_handoff(session_path: Path, output_dir: Path) -> RealWorldQualificationPlan:
    session=load_product_session(Path(session_path));out=Path(output_dir)
    if out.exists() and any(out.iterdir()):raise FileExistsError(f'handoff directory is not empty: {out}')
    out.mkdir(parents=True,exist_ok=True)
    ok,problems=verify_session_sources(session)
    plan=RealWorldQualificationPlan(
        created_utc=_utcnow(),session_id=session.session_id,source_binding=_source_binding(session),
        source_integrity_at_plan_time=ok,source_integrity_problems=problems,
        required_evidence=['real_takeout_qualification','host_diagnostics','windows_core_qualification','windows_writer_qualification','independent_storage_evidence'],
        safety_invariants={
            'takeout_sources_must_remain_byte_identical':True,
            'writer_evidence_does_not_approve_production_writes':True,
            'windows_evidence_does_not_approve_google_retirement':True,
            'unknown_metadata_remains_unknown_or_review':True,
            'raw_embedded_rewriting_remains_prohibited':True,
        },
        generated_files=['QUALIFICATION_PLAN.json','RUN_WINDOWS_QUALIFICATION.ps1','README_WINDOWS_QUALIFICATION.txt'],
        next_steps=['Run qualify-real-takeout against the exact session sources.','Run the PowerShell qualification runner on the target Windows x64 machine.','Attach exact generated evidence to the session and recompute product-status.'],
    )
    write_json_new(out/'QUALIFICATION_PLAN.json',plan.to_dict())
    (out/'RUN_WINDOWS_QUALIFICATION.ps1').write_text(_POWERSHELL,encoding='utf-8',newline='\r\n')
    (out/'README_WINDOWS_QUALIFICATION.txt').write_text(_README,encoding='utf-8',newline='\r\n')
    return plan


def _sanitize_report(d: dict[str, Any], *, include_source_hashes: bool=False) -> dict[str, Any]:
    """Return a strict allow-list of diagnostic facts with no free-form local text."""
    out={'schema':d.get('schema')}
    for k in ('passed','source_bytes_unchanged','zip_readable','archive_exists','archive_audit_ok','source_integrity_ok','retirement_state'):
        v=d.get(k)
        if isinstance(v,(bool,str)) or v is None:out[k]=v
    for k in ('member_count','media_members','json_members','other_members'):
        if isinstance(d.get(k),int):out[k]=d[k]
    checks=d.get('checks')
    if isinstance(checks,dict):out['checks']={str(k):bool(v) for k,v in checks.items() if isinstance(v,bool)}
    gates=d.get('qualification_gates')
    if isinstance(gates,dict):out['qualification_gates']={str(k):bool(v) for k,v in gates.items() if isinstance(v,bool)}
    platform_info=d.get('platform')
    if isinstance(platform_info,dict):
        out['platform']={k:platform_info.get(k) for k in ('system','release','version','machine','processor') if isinstance(platform_info.get(k),(str,int,bool))}
    runtime=d.get('runtime')
    if isinstance(runtime,dict):
        out['runtime']={k:runtime.get(k) for k in ('python_version','python_implementation','pointer_bits','executable_sha256') if isinstance(runtime.get(k),(str,int,bool))}
    ext=d.get('extensions')
    if isinstance(ext,dict):out['extensions']={str(k):int(v) for k,v in ext.items() if isinstance(v,int)}
    preview=d.get('preview')
    if isinstance(preview,dict):
        safe_preview={}
        for k,v in preview.items():
            if isinstance(v,(bool,int,float)) or (isinstance(v,str) and k in {'status'}):safe_preview[str(k)]=v
        out['preview']=safe_preview
    for src,dst in (('errors','error_count'),('warnings','warning_count'),('blockers','blocker_count'),('source_problems','source_problem_count')):
        v=d.get(src)
        if isinstance(v,list):out[dst]=len(v)
    if include_source_hashes and isinstance(d.get('sources'),list):
        out['sources']=[{x:y for x,y in row.items() if x in {'size','sha256','zip_members','source_index'} and isinstance(y,(str,int))} for row in d['sources'] if isinstance(row,dict)]
    return out


def create_privacy_support_bundle(session_path: Path, report_paths: list[Path], output_zip: Path, *, include_source_hashes: bool=False) -> dict[str,Any]:
    session=load_product_session(Path(session_path));output_zip=Path(output_zip)
    if output_zip.exists():raise FileExistsError(output_zip)
    entries:dict[str,bytes]={}
    included=[];rejected=[]
    for i,rp in enumerate(report_paths,1):
        p=Path(rp)
        try:
            d=json.loads(p.read_text(encoding='utf-8'))
            if not isinstance(d,dict):raise ValueError('report is not a JSON object')
            clean=_sanitize_report(d,include_source_hashes=include_source_hashes)
            name=f'report_{i:02d}.json'
            entries[name]=(json.dumps(clean,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
            included.append({'index':i,'schema':clean.get('schema'),'sha256':hashlib.sha256(entries[name]).hexdigest()})
        except Exception as e:rejected.append({'index':i,'reason':f'{type(e).__name__}: {e}'})
    manifest={
        'schema':SUPPORT_SCHEMA,'created_utc':_utcnow(),'session_id_sha256':hashlib.sha256(session.session_id.encode()).hexdigest(),
        'source_summary':_source_binding(session,include_hashes=include_source_hashes),
        'include_source_hashes':include_source_hashes,'included_reports':included,'rejected_reports':rejected,
        'takeout_bytes_included':False,'source_paths_included':False,'source_filenames_included':False,
        'production_write_approved':False,'google_retirement_approved':False,
    }
    entries['manifest.json']=(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    output_zip.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output_zip,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in sorted(entries):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            z.writestr(info,entries[name],compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    manifest['bundle_sha256']=_sha256(output_zip)
    manifest['bundle_size']=output_zip.stat().st_size
    return manifest
