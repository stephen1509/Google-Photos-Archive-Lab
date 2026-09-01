from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib, json, os
from pathlib import Path, PurePosixPath
from typing import Callable

from .output import portable_filename
from .transactions import commit_no_overwrite, sha256_file, stage_copy, write_json_new

@dataclass(frozen=True)
class VaultedSource:
    original_path:str
    original_name:str
    sha256:str
    size:int
    vaulted_path:str
    record_path:str
    reused_existing_bytes:bool=False

@dataclass
class SourceVaultAudit:
    records_checked:int=0
    archives_checked:int=0
    problems:list[str]=field(default_factory=list)
    digests:set[str]=field(default_factory=set)
    @property
    def ok(self)->bool:return not self.problems


def _occurrence_id(source:Path,digest:str)->str:
    try:resolved=str(source.resolve())
    except OSError:resolved=str(source)
    return hashlib.sha256((resolved+'\0'+digest).encode('utf-8')).hexdigest()[:16]


def vault_source_archive(source:Path,vault_root:Path,*,after_bytes_commit:Callable[[Path],None]|None=None)->VaultedSource:
    """Copy one raw Takeout archive into a content-addressed source vault."""
    source=Path(source);vault_root=Path(vault_root)
    st=source.stat();digest=sha256_file(source);suffix=source.suffix.lower() if source.suffix else '.bin'
    bytes_dir=vault_root/'archives'/digest[:2];dest=bytes_dir/f'{digest}{suffix}'
    reused=False
    if dest.exists():
        if dest.stat().st_size!=st.st_size or sha256_file(dest)!=digest:
            raise RuntimeError(f'source-vault content-address collision/corruption: {dest}')
        reused=True
    else:
        staged=stage_copy(source,bytes_dir,digest)
        try:
            commit_no_overwrite(staged,dest)
        except Exception:
            # This stage is only a derived copy of the still-authoritative raw
            # source. Retaining an unreferenced stage after ENOSPC/EIO would not
            # make retries resumable and can worsen a full-disk condition.
            try:staged.unlink(missing_ok=True)
            except OSError:pass
            raise
        try:
            verified=(dest.stat().st_size==st.st_size and sha256_file(dest)==digest)
        except OSError:
            verified=False
        if not verified:
            # No provenance record exists yet, and the raw source remains the
            # preservation authority. Remove this newly-created unverified object
            # so a transient device/read failure does not create a permanent
            # content-address collision on the next retry.
            try:dest.unlink(missing_ok=True)
            except OSError:pass
            raise RuntimeError(f'source-vault verification failed: {dest}')
        # Exact fault boundary: durable verified bytes exist, provenance does not.
        if after_bytes_commit:after_bytes_commit(dest)
    occ=_occurrence_id(source,digest);record=vault_root/'records'/f'{digest}__{occ}.json'
    obj={
        'schema':'gpa.source-vault-record.v1','sha256':digest,'size':st.st_size,
        'original_path':str(source),'original_name':source.name,
        'portable_original_name':portable_filename(source.name),
        'vaulted_path':dest.relative_to(vault_root).as_posix(),
        'source_mtime_ns':st.st_mtime_ns,'recorded_utc':datetime.now(timezone.utc).isoformat(),
    }
    if record.exists():
        old=json.loads(record.read_text(encoding='utf-8'))
        stable=('schema','sha256','size','original_path','original_name','portable_original_name','vaulted_path','source_mtime_ns')
        if any(old.get(k)!=obj.get(k) for k in stable):raise RuntimeError(f'source-vault provenance record collision: {record}')
    else:write_json_new(record,obj)
    return VaultedSource(str(source),source.name,digest,st.st_size,dest.relative_to(vault_root).as_posix(),record.relative_to(vault_root).as_posix(),reused)


def vault_source_archives(sources:list[Path|str],vault_root:Path)->list[VaultedSource]:
    return [vault_source_archive(Path(s),vault_root) for s in sources]


def _is_linkish(path:Path)->bool:
    try:
        if path.is_symlink():return True
        is_junction=getattr(path,'is_junction',None)
        return bool(is_junction and is_junction())
    except OSError:return True


def _safe_vault_rel(rel:str)->Path|None:
    if not isinstance(rel,str) or not rel or '\x00' in rel or '\\' in rel:return None
    posix=PurePosixPath(rel)
    if posix.is_absolute() or any(x in {'','.', '..'} for x in rel.split('/')):return None
    return Path(*posix.parts)


def audit_source_vault(vault_root:Path)->SourceVaultAudit:
    vault_root=Path(vault_root);out=SourceVaultAudit();records=vault_root/'records';archives=vault_root/'archives'
    if not records.exists():out.problems.append('source-vault records directory is missing');return out
    if _is_linkish(vault_root) or _is_linkish(records) or (archives.exists() and _is_linkish(archives)):
        out.problems.append('source-vault root/records/archives must not be a symlink or junction');return out
    verified_by_rel={};referenced_rels=set()
    try:
        record_paths=sorted(records.glob('*.json'))
    except OSError as e:
        out.problems.append(f'source-vault records directory unreadable: {e}');return out
    for rp in record_paths:
        out.records_checked+=1
        if _is_linkish(rp):out.problems.append(f'{rp}: provenance record is a symlink/junction');continue
        try:obj=json.loads(rp.read_text(encoding='utf-8'))
        except Exception as e:out.problems.append(f'{rp}: unreadable record: {e}');continue
        if obj.get('schema')!='gpa.source-vault-record.v1':out.problems.append(f'{rp}: unsupported schema');continue
        digest=obj.get('sha256');rel=obj.get('vaulted_path');size=obj.get('size')
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):out.problems.append(f'{rp}: invalid sha256');continue
        if not isinstance(size,int) or size<0:out.problems.append(f'{rp}: invalid size');continue
        safe=_safe_vault_rel(rel)
        if safe is None:out.problems.append(f'{rp}: invalid vaulted path');continue
        p=vault_root/safe
        try:p.resolve().relative_to(vault_root.resolve())
        except Exception:out.problems.append(f'{rp}: vaulted path escapes vault');continue
        cur=vault_root;unsafe=False
        for part in safe.parts:
            cur=cur/part
            if cur.exists() and _is_linkish(cur):unsafe=True;break
        if unsafe:out.problems.append(f'{rp}: vaulted path contains symlink/junction');continue
        if not p.is_file():out.problems.append(f'{rp}: vaulted archive is missing');continue
        binding=(size,digest);prior=verified_by_rel.get(rel)
        if prior is not None and prior!=binding:
            out.problems.append(f'{rp}: occurrence record disagrees with verified byte-object binding');continue
        if prior is None:
            try:
                matches=(p.stat().st_size==size and sha256_file(p)==digest)
            except OSError as e:
                out.problems.append(f'{rp}: vaulted archive unreadable: {e}');continue
            if not matches:
                out.problems.append(f'{rp}: vaulted archive hash/size mismatch');continue
            verified_by_rel[rel]=binding;out.archives_checked+=1
        referenced_rels.add(rel);out.digests.add(digest)
    if archives.exists():
        try:
            archive_paths=sorted(archives.rglob('*'))
        except OSError as e:
            out.problems.append(f'source-vault archives directory unreadable: {e}');return out
        for p in archive_paths:
            if _is_linkish(p):out.problems.append(f'{p}: source-vault archive tree contains symlink/junction');continue
            if not p.is_file():continue
            rel=p.relative_to(vault_root).as_posix()
            if rel not in referenced_rels:out.problems.append(f'{p}: orphan vaulted byte object has no provenance record')
    return out
