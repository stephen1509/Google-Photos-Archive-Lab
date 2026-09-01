from __future__ import annotations
import errno, hashlib, json, os, shutil, tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

class CommitError(Exception):pass
class ProjectionConflict(CommitError):pass


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()

def fsync_dir(path:Path)->None:
    """Fsync a directory where supported, but never hide real storage failures.

    Some platforms/filesystems do not support directory fsync at all. Those
    capability errors remain best-effort for portability. Once a directory
    descriptor is open, however, durability failures such as ENOSPC/EIO must
    propagate so callers cannot report a commit as durable when it is not.
    """
    try:
        fd=os.open(path,os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as e:
        unsupported={errno.EINVAL,errno.EBADF}
        for name in ('ENOTSUP','EOPNOTSUPP'):
            value=getattr(errno,name,None)
            if value is not None:unsupported.add(value)
        if e.errno not in unsupported:raise
    finally:
        os.close(fd)

def stage_copy(src:Path,dest_dir:Path,expected_sha256:str|None=None)->Path:
    dest_dir.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.gpa-stage-',dir=dest_dir)
    os.close(fd);p=Path(tmp)
    try:
        shutil.copyfile(src,p)
        if expected_sha256 and sha256_file(p)!=expected_sha256:raise CommitError('staged hash mismatch')
        # Windows requires a writable handle for FlushFileBuffers/os.fsync.
        # The staged copy is private to this transaction, so opening it read/write
        # preserves the durability guarantee without changing its contents.
        with p.open('r+b') as f:os.fsync(f.fileno())
        return p
    except Exception:
        p.unlink(missing_ok=True);raise

def stage_bytes(data:bytes,dest_dir:Path)->Path:
    dest_dir.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.gpa-stage-',dir=dest_dir)
    p=Path(tmp)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        return p
    except Exception:
        p.unlink(missing_ok=True);raise

def _cleanup_created_destination(dest:Path)->None:
    """Remove an uncommitted destination without masking the primary failure."""
    try:
        dest.unlink()
    except FileNotFoundError:
        return
    try:
        fsync_dir(dest.parent)
    except OSError:
        # The primary commit failure is more useful to the caller. A remaining
        # durability uncertainty is recoverable because the staged bytes stay.
        pass

def commit_no_overwrite(staged:Path,dest:Path)->None:
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():raise FileExistsError(dest)

    # Preferred path: same-filesystem hardlink gives atomic no-overwrite. Keep
    # link creation separate from namespace durability confirmation so an fsync
    # failure is never mistaken for "hardlink unsupported".
    try:
        os.link(staged,dest)
    except FileExistsError:
        raise
    except OSError as link_error:
        # Conservative fallback: exclusive-create + copy, then fsync. This is
        # appropriate for cross-device/unsupported-link cases. Storage failures
        # (ENOSPC/EIO/etc.) fail closed without attempting a second write path.
        fallback_errnos={errno.EXDEV,errno.EPERM,errno.EACCES,errno.EINVAL}
        for name in ('ENOTSUP','EOPNOTSUPP'):
            value=getattr(errno,name,None)
            if value is not None:fallback_errnos.add(value)
        if link_error.errno not in fallback_errnos:
            raise CommitError(f'hardlink commit failed: {link_error}') from link_error
        created=False
        try:
            fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o666);created=True
            with os.fdopen(fd,'wb') as out,staged.open('rb') as inp:
                shutil.copyfileobj(inp,out);out.flush();os.fsync(out.fileno())
            fsync_dir(dest.parent)
            staged.unlink()
            return
        except Exception as e:
            if created:_cleanup_created_destination(dest)
            raise CommitError(f'commit failed after hardlink fallback: {link_error}; copy error: {e}') from e

    try:
        fsync_dir(dest.parent)
    except OSError as e:
        _cleanup_created_destination(dest)
        raise CommitError(f'hardlink commit durability confirmation failed: {e}') from e
    staged.unlink()

def write_json_new(path:Path,obj:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o666)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        fsync_dir(path.parent)
    except Exception:
        # The record is not durable until its parent directory fsync succeeds.
        # Remove the unconfirmed destination where possible, but never allow a
        # cleanup fsync failure to replace the original storage/durability error.
        _cleanup_created_destination(path)
        raise

@dataclass(frozen=True)
class ProjectionCopy:
    src:Path
    dest:Path
    sha256:str

@dataclass(frozen=True)
class ProjectionBytes:
    dest:Path
    data:bytes


def _ensure_existing_hash(path:Path,expected:str)->None:
    if sha256_file(path)!=expected:
        raise ProjectionConflict(f'destination exists with different content: {path}')

def apply_projection_update(
    copies:list[ProjectionCopy],
    generated:list[ProjectionBytes],
    remove_after:list[Path],
    after_commit:Callable[[int,Path],None]|None=None,
)->None:
    """Crash-resumable projection update.

    All new destinations are created and verified before *any* old projection path is removed.
    Re-running after a crash is idempotent: matching destinations are accepted; conflicting
    destinations stop the transaction before cleanup.
    """
    expected_generated={g.dest:hashlib.sha256(g.data).hexdigest() for g in generated}
    # Preflight existing destinations before making any change.
    for c in copies:
        if c.dest.exists():_ensure_existing_hash(c.dest,c.sha256)
        elif not c.src.exists():raise CommitError(f'missing projection source: {c.src}')
        elif sha256_file(c.src)!=c.sha256:raise CommitError(f'projection source hash mismatch: {c.src}')
    for g in generated:
        if g.dest.exists():_ensure_existing_hash(g.dest,expected_generated[g.dest])

    count=0
    for c in copies:
        if not c.dest.exists():
            staged=stage_copy(c.src,c.dest.parent,c.sha256)
            commit_no_overwrite(staged,c.dest)
        _ensure_existing_hash(c.dest,c.sha256)
        count+=1
        if after_commit:after_commit(count,c.dest)
    for g in generated:
        expected=expected_generated[g.dest]
        if not g.dest.exists():
            staged=stage_bytes(g.data,g.dest.parent)
            commit_no_overwrite(staged,g.dest)
        _ensure_existing_hash(g.dest,expected)
        count+=1
        if after_commit:after_commit(count,g.dest)

    # Only once the entire new projection exists and verifies do old projection paths disappear.
    destinations={c.dest.resolve() for c in copies}|{g.dest.resolve() for g in generated}
    for old in remove_after:
        try:
            if old.resolve() in destinations:continue
        except OSError:
            pass
        if old.exists():
            old.unlink();fsync_dir(old.parent)
