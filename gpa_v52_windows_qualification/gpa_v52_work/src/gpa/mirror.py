from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable
import hashlib

from .transactions import commit_no_overwrite, stage_copy


class MirrorConflict(RuntimeError):
    pass


def _is_linkish(path:Path)->bool:
    path=Path(path)
    try:
        if path.is_symlink():return True
        is_junction=getattr(path,'is_junction',None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _unsafe_component(root:Path,path:Path)->Path|None:
    root=Path(root);path=Path(path)
    try:rel=path.relative_to(root)
    except ValueError:return path
    cur=root
    if cur.exists() and _is_linkish(cur):return cur
    for part in rel.parts:
        cur=cur/part
        if cur.exists() and _is_linkish(cur):return cur
    return None


@dataclass
class MirrorReport:
    files_expected:int=0
    files_checked:int=0
    missing:list[str]=field(default_factory=list)
    mismatched:list[str]=field(default_factory=list)
    extra:list[str]=field(default_factory=list)
    invalid_manifest:list[str]=field(default_factory=list)
    unsafe_paths:list[str]=field(default_factory=list)
    @property
    def ok(self):return not self.missing and not self.mismatched and not self.invalid_manifest and not self.unsafe_paths


@dataclass
class MirrorBuildReport:
    files_expected:int=0
    files_copied:int=0
    files_reused:int=0
    files_verified:int=0
    verification:MirrorReport|None=None
    @property
    def ok(self)->bool:
        return self.verification is not None and self.verification.ok and self.files_verified==self.files_expected


def file_sha256(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()


def _manifest_rows(manifest:dict)->list[dict]:
    if not isinstance(manifest,dict) or manifest.get('schema')!='gpa.tree-manifest.v1':
        raise ValueError('unsupported tree manifest schema')
    raw=manifest.get('files')
    if not isinstance(raw,list):raise ValueError('tree manifest files must be a list')
    rows=[];seen=set()
    for i,row in enumerate(raw):
        if not isinstance(row,dict):raise ValueError(f'invalid tree manifest row {i}')
        rel=row.get('path');size=row.get('size');digest=row.get('sha256')
        if not isinstance(rel,str) or not rel or '\x00' in rel or '\\' in rel:raise ValueError('invalid tree manifest path')
        pp=PurePosixPath(rel);parts=rel.split('/')
        if pp.is_absolute() or any(x in {'','.', '..'} for x in parts):raise ValueError(f'unsafe tree manifest path: {rel}')
        if rel in seen:raise ValueError(f'duplicate tree manifest path: {rel}')
        if not isinstance(size,int) or size<0:raise ValueError(f'invalid tree manifest size: {rel}')
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError(f'invalid tree manifest sha256: {rel}')
        seen.add(rel);rows.append({'path':rel,'size':size,'sha256':digest})
    return rows


def _safe_path(root:Path,rel:str,*,allow_missing:bool)->Path:
    root=Path(root)
    if root.exists() and _is_linkish(root):raise MirrorConflict(f'mirror root is a symlink/junction: {root}')
    p=root
    for part in PurePosixPath(rel).parts:
        p=p/part
        if p.exists() and _is_linkish(p):raise MirrorConflict(f'symlink/junction is not allowed in mirror tree: {p}')
    if not allow_missing and not p.exists():raise FileNotFoundError(p)
    return p


def build_tree_manifest(root:Path,*,exclude_names:set[str]|None=None)->dict:
    root=Path(root);exclude_names=exclude_names or set();rows=[]
    if not root.is_dir():raise ValueError(f'manifest root is not a directory: {root}')
    if _is_linkish(root):raise MirrorConflict(f'tree root is a symlink/junction: {root}')
    for p in sorted(root.rglob('*')):
        if _is_linkish(p):raise MirrorConflict(f'symlink/junction is not allowed in manifest tree: {p}')
        if not p.is_file():continue
        rel=p.relative_to(root).as_posix()
        if p.name in exclude_names:continue
        rows.append({'path':rel,'size':p.stat().st_size,'sha256':file_sha256(p)})
    return {'schema':'gpa.tree-manifest.v1','files':rows}


def verify_mirror(manifest:dict,mirror_root:Path,*,report_extras:bool=False)->MirrorReport:
    mirror_root=Path(mirror_root);out=MirrorReport()
    try:rows=_manifest_rows(manifest)
    except ValueError as e:
        out.invalid_manifest.append(str(e));return out
    expected={r['path']:r for r in rows};out.files_expected=len(expected)
    if mirror_root.exists() and _is_linkish(mirror_root):
        out.unsafe_paths.append('.');return out
    for row in rows:
        rel=row['path']
        try:p=_safe_path(mirror_root,rel,allow_missing=True)
        except MirrorConflict:
            out.unsafe_paths.append(rel);continue
        if not p.exists() or not p.is_file():out.missing.append(rel);continue
        out.files_checked+=1
        try:
            matches=(p.stat().st_size==row['size'] and file_sha256(p)==row['sha256'])
        except OSError:
            matches=False
        if not matches:out.mismatched.append(rel)
    if report_extras and mirror_root.exists():
        actual=set()
        for p in mirror_root.rglob('*'):
            rel=p.relative_to(mirror_root).as_posix()
            if _is_linkish(p):
                out.unsafe_paths.append(rel);continue
            if p.is_file():actual.add(rel)
        out.extra=sorted(actual-set(expected))
    out.unsafe_paths=sorted(set(out.unsafe_paths))
    return out


def materialize_verified_mirror(
    manifest:dict,source_root:Path,mirror_root:Path,*,
    after_commit:Callable[[int,Path],None]|None=None,
)->MirrorBuildReport:
    """Create or resume an exact verified copy with full preflight and no overwrite."""
    source_root=Path(source_root);mirror_root=Path(mirror_root);rows=_manifest_rows(manifest)
    if not source_root.is_dir() or _is_linkish(source_root):raise MirrorConflict(f'source root is missing or link-like: {source_root}')
    if mirror_root.exists() and _is_linkish(mirror_root):raise MirrorConflict(f'mirror root is a symlink/junction: {mirror_root}')

    prepared=[]
    for row in rows:
        rel=row['path'];src=_safe_path(source_root,rel,allow_missing=False);dst=_safe_path(mirror_root,rel,allow_missing=True)
        if not src.is_file():raise MirrorConflict(f'mirror source is not a file: {src}')
        if src.stat().st_size!=row['size'] or file_sha256(src)!=row['sha256']:
            raise MirrorConflict(f'mirror source changed since manifest: {src}')
        if dst.exists() and (not dst.is_file() or dst.stat().st_size!=row['size'] or file_sha256(dst)!=row['sha256']):
            raise MirrorConflict(f'mirror destination conflict: {dst}')
        prepared.append((row,src,dst))

    out=MirrorBuildReport(files_expected=len(prepared));count=0
    for row,src,dst in prepared:
        copied_new=False
        if dst.exists():out.files_reused+=1
        else:
            staged=stage_copy(src,dst.parent,row['sha256'])
            try:
                commit_no_overwrite(staged,dst)
            except FileExistsError:
                staged.unlink(missing_ok=True)
                if _is_linkish(dst) or not dst.is_file() or dst.stat().st_size!=row['size'] or file_sha256(dst)!=row['sha256']:
                    raise MirrorConflict(f'mirror destination conflict after preflight: {dst}')
                out.files_reused+=1
            except Exception:
                # The staged mirror object is reproducible from the verified
                # source. Do not leak unreferenced stage files across retries,
                # especially after ENOSPC/device-loss failures.
                try:staged.unlink(missing_ok=True)
                except OSError:pass
                raise
            else:
                out.files_copied+=1;copied_new=True
        try:
            verified=(not _is_linkish(dst) and dst.is_file() and dst.stat().st_size==row['size'] and file_sha256(dst)==row['sha256'])
        except OSError:
            verified=False
        if not verified:
            if copied_new:
                try:dst.unlink(missing_ok=True)
                except OSError:pass
            raise MirrorConflict(f'mirror verification failed after commit: {dst}')
        out.files_verified+=1;count+=1
        if after_commit:after_commit(count,dst)

    out.verification=verify_mirror(manifest,mirror_root)
    if not out.verification.ok:raise MirrorConflict('mirror final verification failed')
    return out


def synchronize_mirror(
    manifest:dict,source_root:Path,mirror_root:Path,*,
    after_commit:Callable[[int,Path],None]|None=None,
)->MirrorReport:
    """Compatibility wrapper returning the final MirrorReport."""
    out=materialize_verified_mirror(manifest,source_root,mirror_root,after_commit=after_commit)
    assert out.verification is not None
    return out.verification
