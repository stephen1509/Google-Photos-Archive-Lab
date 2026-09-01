from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
import hashlib
import json
import os
import uuid
import zipfile

from .executor import RunExecutor
from .preview import build_run_preview
from .audit import audit_archive
from .verification import verify_migration
from .transactions import write_json_new
from .zipindex import ZipLibrary

SESSION_SCHEMA='gpa.product-session.v1'
QUALIFICATION_SCHEMA='gpa.real-takeout-qualification.v1'
DASHBOARD_SCHEMA='gpa.product-dashboard.v1'


def _utcnow()->str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class SourceFingerprint:
    path:str
    name:str
    size:int
    mtime_ns:int
    sha256:str
    zip_members:int

    def to_dict(self)->dict:return asdict(self)


@dataclass
class ProductSession:
    schema:str=SESSION_SCHEMA
    session_id:str=''
    created_at:str=''
    workspace:str=''
    sources:list[SourceFingerprint]=field(default_factory=list)
    archive_output:str=''
    source_vault:str|None=None
    redundant_archive:str|None=None
    redundant_source_vault:str|None=None
    takeout_receipts:list[str]=field(default_factory=list)
    exit_evidence:str|None=None
    real_takeout_qualification:str|None=None
    windows_core_qualification:str|None=None
    windows_writer_qualification:str|None=None
    apple_photos_qualification:str|None=None
    all_takeout_parts_confirmed:bool=False
    redundant_archive_separate_media_confirmed:bool=False
    redundant_source_vault_separate_media_confirmed:bool=False
    require_apple_photos:bool=False
    notes:list[str]=field(default_factory=list)

    def to_dict(self)->dict:
        d=asdict(self);d['sources']=[x.to_dict() for x in self.sources];return d


@dataclass
class RealTakeoutQualification:
    schema:str=QUALIFICATION_SCHEMA
    created_at:str=''
    sources:list[dict]=field(default_factory=list)
    source_bytes_unchanged:bool=False
    zip_readable:bool=False
    member_count:int=0
    media_members:int=0
    json_members:int=0
    other_members:int=0
    extensions:dict[str,int]=field(default_factory=dict)
    top_level_roots:dict[str,int]=field(default_factory=dict)
    preview:dict=field(default_factory=dict)
    source_problems:list[dict]=field(default_factory=list)
    passed:bool=False
    blockers:list[str]=field(default_factory=list)

    def to_dict(self)->dict:return asdict(self)


@dataclass
class ProductDashboard:
    schema:str=DASHBOARD_SCHEMA
    generated_at:str=''
    session_id:str=''
    source_integrity_ok:bool=False
    source_integrity_problems:list[str]=field(default_factory=list)
    preview_status:str='not_run'
    preview:dict=field(default_factory=dict)
    archive_exists:bool=False
    archive_audit_ok:bool=False
    archive_audit:dict=field(default_factory=dict)
    migration_verification:dict=field(default_factory=dict)
    qualification_gates:dict[str,bool]=field(default_factory=dict)
    blockers:list[str]=field(default_factory=list)
    retirement_state:str='NOT_READY'

    def to_dict(self)->dict:return asdict(self)


def fingerprint_source(path:Path)->SourceFingerprint:
    p=Path(path)
    st=p.stat()
    if not p.is_file():raise ValueError(f'not a regular file: {p}')
    if p.suffix.lower()!='.zip':raise ValueError(f'not a ZIP archive: {p}')
    with zipfile.ZipFile(p) as z:
        bad=z.testzip()
        if bad is not None:raise zipfile.BadZipFile(f'CRC failure in ZIP member: {bad}')
        count=len([x for x in z.infolist() if not x.is_dir()])
    st2=p.stat()
    if (st.st_size,st.st_mtime_ns)!=(st2.st_size,st2.st_mtime_ns):
        raise RuntimeError(f'source changed during ZIP inspection: {p}')
    digest=_sha256_file(p)
    st3=p.stat()
    if (st.st_size,st.st_mtime_ns)!=(st3.st_size,st3.st_mtime_ns):
        raise RuntimeError(f'source changed during fingerprinting: {p}')
    return SourceFingerprint(str(p.resolve()),p.name,st.st_size,st.st_mtime_ns,digest,count)


def create_product_session(workspace:Path,sources:Iterable[Path],archive_output:Path,**kwargs)->ProductSession:
    workspace=Path(workspace);workspace.mkdir(parents=True,exist_ok=True)
    session_path=workspace/'session.json'
    if session_path.exists():raise FileExistsError(session_path)
    source_paths=[Path(p) for p in sources]
    if not source_paths:raise ValueError('at least one Takeout ZIP is required')
    resolved=[str(p.resolve()) for p in source_paths]
    if len(set(resolved))!=len(resolved):raise ValueError('duplicate source ZIP path supplied')
    fps=[fingerprint_source(p) for p in source_paths]
    s=ProductSession(session_id=str(uuid.uuid4()),created_at=_utcnow(),workspace=str(workspace.resolve()),sources=fps,archive_output=str(Path(archive_output).resolve()),**kwargs)
    write_json_new(session_path,s.to_dict())
    return s


def load_product_session(path:Path)->ProductSession:
    p=Path(path);d=json.loads(p.read_text(encoding='utf-8'))
    if d.get('schema')!=SESSION_SCHEMA:raise ValueError(f'unsupported product session schema: {d.get("schema")}')
    d=dict(d);d['sources']=[SourceFingerprint(**x) for x in d.get('sources',[])]
    return ProductSession(**d)


def write_product_session(path:Path,session:ProductSession)->Path:
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(session.to_dict(),ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_bytes(data);os.replace(tmp,p);return p


def verify_session_sources(session:ProductSession)->tuple[bool,list[str]]:
    problems=[]
    for expected in session.sources:
        p=Path(expected.path)
        try:
            st=p.stat()
            if (st.st_size,st.st_mtime_ns)!=(expected.size,expected.mtime_ns):
                problems.append(f'{p}: size or modification time changed since session creation');continue
            got=_sha256_file(p)
            if got!=expected.sha256:problems.append(f'{p}: SHA-256 changed since session creation')
        except OSError as e:problems.append(f'{p}: unavailable: {e}')
    return not problems,problems


def qualify_real_takeout(sources:Iterable[Path],output_root:Path,report_path:Path|None=None)->RealTakeoutQualification:
    paths=[Path(x) for x in sources]
    before=[fingerprint_source(x) for x in paths]
    q=RealTakeoutQualification(created_at=_utcnow(),sources=[x.to_dict() for x in before])
    try:
        lib=ZipLibrary(paths)
        members,problems=lib.scan_tolerant()
        q.member_count=len(members);q.source_problems=[asdict(x) for x in problems]
        q.zip_readable=not any(x.kind=='invalid_zip' for x in problems)
        media_ext={'.jpg','.jpeg','.png','.gif','.heic','.heif','.avif','.webp','.mp4','.mov','.m4v','.3gp','.avi','.mts','.m2ts','.dng','.cr2','.cr3','.nef','.arw','.raf','.orf','.rw2','.pef'}
        for m in members:
            ext=m.suffix or '<none>';q.extensions[ext]=q.extensions.get(ext,0)+1
            root=m.path.split('/',1)[0] if '/' in m.path else '<root>';q.top_level_roots[root]=q.top_level_roots.get(root,0)+1
            if ext in media_ext:q.media_members+=1
            elif ext=='.json':q.json_members+=1
            else:q.other_members+=1
        ex=RunExecutor(paths,Path(output_root))
        report=ex.plan();preview=build_run_preview(ex,report)
        q.preview=preview.to_dict()
        if problems:q.blockers.append(f'{len(problems)} source problem(s) were detected.')
        if preview.takeout_sequence_known_gaps:q.blockers.append(f'{preview.takeout_sequence_known_gaps} known Takeout part gap(s) were detected.')
        if preview.source_problems:q.blockers.append(f'{preview.source_problems} planning source problem(s) were detected.')
        if preview.unclassified_items:q.blockers.append(f'{preview.unclassified_items} unclassified Takeout item(s) require review before representative qualification can pass.')
        if preview.blocking_review_items:q.blockers.append(f'{preview.blocking_review_items} blocking review item(s) remain unresolved.')
        if not preview.source_understanding_complete:q.blockers.append('Source understanding is incomplete; representative qualification cannot pass yet.')
        if not preview.can_execute:q.blockers.append('Read-only planning cannot execute safely against the selected destination.')
    except Exception as e:
        q.blockers.append(f'Qualification aborted: {e}')
    after=[]
    for x in paths:
        try:after.append(fingerprint_source(x))
        except Exception as e:q.blockers.append(f'Could not re-fingerprint source {x}: {e}')
    q.source_bytes_unchanged=len(after)==len(before) and all(a.sha256==b.sha256 and a.size==b.size for a,b in zip(before,after))
    if not q.source_bytes_unchanged:q.blockers.append('One or more source ZIP byte fingerprints changed during qualification.')
    q.passed=bool(q.zip_readable and q.source_bytes_unchanged and q.preview and not q.blockers)
    if report_path:
        p=Path(report_path);p.parent.mkdir(parents=True,exist_ok=True)
        write_json_new(p,q.to_dict())
    return q


def _read_json_object(path:str|None)->dict|None:
    if not path:return None
    try:
        d=json.loads(Path(path).read_text(encoding='utf-8'))
        return d if isinstance(d,dict) else None
    except Exception:return None


def _all_checks_true(d:dict)->bool:
    checks=d.get('checks')
    return isinstance(checks,dict) and bool(checks) and all(v is True for v in checks.values())


def _windows_platform_from_report(d:dict)->dict:
    if d.get('schema')=='gpa.windows-core-qualification.v1':return d.get('platform') or {}
    if d.get('schema')=='gpa.windows-release-qualification.v1':return ((d.get('windows_core') or {}).get('platform') or {})
    if d.get('schema')=='gpa.windows-writer-qualification.v1':return ((((d.get('windows_release') or {}).get('windows_core') or {}).get('platform')) or {})
    return {}


def _windows_qualification_pass(path:str|None,accepted_schemas:tuple[str,...])->bool:
    d=_read_json_object(path)
    if not d or d.get('schema') not in accepted_schemas:return False
    if d.get('passed') is not True or d.get('errors') not in ([],None):return False
    if not _all_checks_true(d):return False
    platform_info=_windows_platform_from_report(d)
    if platform_info.get('system')!='Windows' or platform_info.get('windows_x64_eligible') is not True:return False
    # Evidence collection is not itself governance approval; require that safety invariant too.
    if d.get('production_write_approved') is not False or d.get('google_retirement_approved') is not False:return False
    return True


def _real_takeout_qualification_pass(path:str|None,session:ProductSession)->bool:
    d=_read_json_object(path)
    if not d or d.get('schema')!=QUALIFICATION_SCHEMA or d.get('passed') is not True:return False
    if d.get('source_bytes_unchanged') is not True or d.get('zip_readable') is not True:return False
    if d.get('blockers') not in ([],None) or d.get('source_problems') not in ([],None):return False
    got=sorted((str(x.get('sha256')),int(x.get('size') or -1)) for x in d.get('sources',[]) if isinstance(x,dict))
    expected=sorted((x.sha256,x.size) for x in session.sources)
    return got==expected and len(got)==len(session.sources)


def _apple_qualification_pass(path:str|None)->bool:
    d=_read_json_object(path)
    if not d or d.get('schema')!='gpa.apple-photos-qualification.v1':return False
    return d.get('passed') is True and d.get('errors') in ([],None) and _all_checks_true(d)


def update_product_session(path:Path,**updates)->ProductSession:
    p=Path(path);s=load_product_session(p)
    allowed={
        'source_vault','redundant_archive','redundant_source_vault','exit_evidence',
        'real_takeout_qualification','windows_core_qualification','windows_writer_qualification',
        'apple_photos_qualification','all_takeout_parts_confirmed',
        'redundant_archive_separate_media_confirmed','redundant_source_vault_separate_media_confirmed',
        'require_apple_photos','takeout_receipts','notes',
    }
    unknown=set(updates)-allowed
    if unknown:raise ValueError('unsupported product session field(s): '+', '.join(sorted(unknown)))
    for k,v in updates.items():setattr(s,k,v)
    write_product_session(p,s);return s

def build_product_dashboard(session:ProductSession)->ProductDashboard:
    d=ProductDashboard(generated_at=_utcnow(),session_id=session.session_id)
    d.source_integrity_ok,d.source_integrity_problems=verify_session_sources(session)
    source_paths=[Path(x.path) for x in session.sources]
    output=Path(session.archive_output)
    try:
        ex=RunExecutor(source_paths,output);report=ex.plan();pv=build_run_preview(ex,report)
        d.preview=pv.to_dict();d.preview_status=pv.status
    except Exception as e:
        d.preview_status='error';d.preview={'error':str(e)}
    d.archive_exists=output.exists()
    if d.archive_exists:
        try:
            ar=audit_archive(output);d.archive_audit_ok=ar.ok
            d.archive_audit={'ok':ar.ok,'assets_checked':ar.assets_checked,'revisions_checked':ar.revisions_checked,'blobs_checked':ar.blobs_checked,'unclassified_checked':ar.unclassified_checked,'problems':[vars(x) for x in ar.problems]}
        except Exception as e:d.archive_audit={'ok':False,'error':str(e)}
        try:
            v=verify_migration(
                output,
                all_takeout_parts_confirmed=session.all_takeout_parts_confirmed,
                redundant_copy_root=Path(session.redundant_archive) if session.redundant_archive else None,
                source_vault_root=Path(session.source_vault) if session.source_vault else None,
                source_vault_redundant_copy_root=Path(session.redundant_source_vault) if session.redundant_source_vault else None,
                redundant_copy_separate_media_confirmed=session.redundant_archive_separate_media_confirmed,
                source_vault_redundant_separate_media_confirmed=session.redundant_source_vault_separate_media_confirmed,
                takeout_receipts=[Path(x) for x in session.takeout_receipts],
                exit_evidence_path=Path(session.exit_evidence) if session.exit_evidence else None,
            )
            d.migration_verification=v.to_dict()
        except Exception as e:d.migration_verification={'google_retirement_ready':False,'blockers':[str(e)]}
    gates={
        'source_integrity':d.source_integrity_ok,
        'read_only_preview':d.preview_status=='ready',
        'archive_audit':d.archive_audit_ok,
        'migration_verification':bool(d.migration_verification.get('google_retirement_ready')),
        'real_takeout_qualified':_real_takeout_qualification_pass(session.real_takeout_qualification,session),
        'windows_core_qualified':_windows_qualification_pass(session.windows_core_qualification,('gpa.windows-core-qualification.v1','gpa.windows-release-qualification.v1')),
        'windows_writer_qualified':_windows_qualification_pass(session.windows_writer_qualification,('gpa.windows-writer-qualification.v1',)),
    }
    if session.require_apple_photos:
        gates['apple_photos_qualified']=_apple_qualification_pass(session.apple_photos_qualification)
    d.qualification_gates=gates
    labels={
        'source_integrity':'Source ZIP fingerprints no longer match the session baseline.',
        'read_only_preview':'Read-only planning is blocked or failed.',
        'archive_audit':'The reconstructed archive has not passed its integrity audit.',
        'migration_verification':'Takeout completeness, source vault, redundancy, media validation, exit evidence, or related retirement gates are incomplete.',
        'real_takeout_qualified':'A representative real Google Takeout qualification report has not passed.',
        'windows_core_qualified':'The target Windows environment has not passed core qualification.',
        'windows_writer_qualified':'The exact Windows metadata-writer build/format scope has not passed qualification.',
        'apple_photos_qualified':'Apple Photos acceptance has not passed for this target profile.',
    }
    d.blockers=[labels[k] for k,v in gates.items() if not v]
    d.retirement_state='READY_FOR_FINAL_REVIEW' if gates and all(gates.values()) else 'NOT_READY'
    return d


def write_product_dashboard(path:Path,dashboard:ProductDashboard)->Path:
    return write_json_new(Path(path),dashboard.to_dict())
