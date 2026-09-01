from __future__ import annotations
import hashlib
import json
import os
import posixpath
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
from .model import SourceRef

class ArchiveSafetyError(Exception): pass
class SourceIntegrityError(Exception): pass

_DRIVE = re.compile(r"^[A-Za-z]:")

def safe_member_path(name: str) -> str:
    name = name.replace("\\", "/")
    if name.startswith("/") or _DRIVE.match(name):
        raise ArchiveSafetyError(f"absolute archive path: {name}")
    normalized = posixpath.normpath(name)
    if normalized in ("..", ".") or normalized.startswith("../"):
        raise ArchiveSafetyError(f"archive path traversal: {name}")
    return normalized

@dataclass(frozen=True)
class Member:
    archive: str
    path: str              # normalized safe logical path
    size: int
    compressed_size: int
    crc: int
    zip_name: str | None = None  # exact central-directory name used to read the member

    @property
    def ref(self) -> SourceRef:
        return SourceRef(self.archive, self.path)

    @property
    def logical_dir(self) -> str:
        return str(PurePosixPath(self.path).parent)

    @property
    def basename(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.path).suffix.lower()

    @property
    def open_name(self)->str:
        return self.zip_name or self.path

@dataclass(frozen=True)
class SourceProblem:
    archive:str
    path:str|None
    kind:str
    message:str

class ZipLibrary:
    def __init__(self, archives: Iterable[str], max_ratio: float = 1000.0):
        self.archives = [os.fspath(p) for p in archives]
        self.max_ratio = max_ratio
        self.members: list[Member] = []
        self._by_key: dict[tuple[str,str], Member] = {}

    def scan(self) -> list[Member]:
        out=[];self._by_key={}
        for archive in self.archives:
            try:
                with zipfile.ZipFile(archive) as z:
                    for i in z.infolist():
                        if i.is_dir(): continue
                        p=safe_member_path(i.filename)
                        if i.flag_bits & 0x1:
                            raise ArchiveSafetyError(f"encrypted ZIP member is unsupported: {p}")
                        mode=(i.external_attr >> 16) & 0xFFFF
                        if mode and stat.S_ISLNK(mode):
                            raise ArchiveSafetyError(f"symlink ZIP member is not allowed: {p}")
                        ratio = i.file_size / max(i.compress_size, 1)
                        if ratio > self.max_ratio:
                            raise ArchiveSafetyError(f"suspicious compression ratio {ratio:.1f}: {p}")
                        m=Member(archive,p,i.file_size,i.compress_size,i.CRC,i.filename)
                        key=(archive,p)
                        if key in self._by_key:
                            raise ArchiveSafetyError(f"duplicate normalized member path in archive: {p}")
                        self._by_key[key]=m; out.append(m)
            except zipfile.BadZipFile as e:
                raise SourceIntegrityError(f"invalid zip {archive}") from e
        self.members=out
        return out

    def _read_chunks(self, member:Member, chunk_size:int=1024*1024):
        try:
            with zipfile.ZipFile(member.archive) as z:
                with z.open(member.open_name) as f:
                    while True:
                        chunk=f.read(chunk_size)
                        if not chunk:break
                        yield chunk
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, OSError, KeyError) as e:
            raise SourceIntegrityError(f"failed reading {member.archive}:{member.path}") from e
        except Exception as e:
            if e.__class__.__module__ == "zlib":
                raise SourceIntegrityError(f"failed decompressing {member.archive}:{member.path}") from e
            raise

    def read(self, member: Member) -> bytes:
        return b''.join(self._read_chunks(member))

    def json(self, member: Member) -> dict:
        try:
            return json.loads(self.read(member).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SourceIntegrityError(f"invalid JSON {member.archive}:{member.path}") from e

    def sha256(self, member: Member) -> str:
        h=hashlib.sha256()
        for chunk in self._read_chunks(member):h.update(chunk)
        return h.hexdigest()

    def archive_sha256(self,archive:str|Path)->str:
        h=hashlib.sha256()
        with open(archive,'rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
        return h.hexdigest()

    def extract_to_staging(self,member:Member,dest_dir:Path,expected_sha256:str|None=None)->tuple[Path,str]:
        """Stream one ZIP member to a temporary file and fsync it; never extracts by archive path."""
        dest_dir=Path(dest_dir);dest_dir.mkdir(parents=True,exist_ok=True)
        before=os.stat(member.archive)
        fd,tmp=tempfile.mkstemp(prefix='.gpa-stage-',dir=dest_dir);p=Path(tmp);h=hashlib.sha256()
        try:
            with os.fdopen(fd,'wb') as out:
                for chunk in self._read_chunks(member):
                    out.write(chunk);h.update(chunk)
                out.flush();os.fsync(out.fileno())
            digest=h.hexdigest()
            if expected_sha256 and digest!=expected_sha256:
                raise SourceIntegrityError(f"member hash mismatch {member.archive}:{member.path}")
            after=os.stat(member.archive)
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise SourceIntegrityError(f"archive changed during extraction: {member.archive}")
            return p,digest
        except Exception:
            try:os.close(fd)
            except OSError:pass
            p.unlink(missing_ok=True);raise

    def scan_tolerant(self)->tuple[list[Member],list[SourceProblem]]:
        out=[];problems=[];self._by_key={}
        for archive in self.archives:
            try:
                with zipfile.ZipFile(archive) as z:
                    for i in z.infolist():
                        if i.is_dir():continue
                        try:
                            p=safe_member_path(i.filename)
                            if i.flag_bits & 0x1:raise ArchiveSafetyError(f"encrypted ZIP member is unsupported: {p}")
                            mode=(i.external_attr >> 16)&0xFFFF
                            if mode and stat.S_ISLNK(mode):raise ArchiveSafetyError(f"symlink ZIP member is not allowed: {p}")
                            ratio=i.file_size/max(i.compress_size,1)
                            if ratio>self.max_ratio:raise ArchiveSafetyError(f"suspicious compression ratio {ratio:.1f}: {p}")
                            m=Member(archive,p,i.file_size,i.compress_size,i.CRC,i.filename);key=(archive,p)
                            if key in self._by_key:raise ArchiveSafetyError(f"duplicate normalized member path in archive: {p}")
                            self._by_key[key]=m;out.append(m)
                        except ArchiveSafetyError as e:
                            problems.append(SourceProblem(archive,i.filename,'archive_safety',str(e)))
            except (zipfile.BadZipFile,zipfile.LargeZipFile,OSError) as e:
                problems.append(SourceProblem(archive,None,'invalid_zip',str(e) or 'invalid zip'))
        self.members=out
        return out,problems

    def batch_json_tolerant(self,members:Iterable[Member])->tuple[dict[tuple[str,str],dict],list[SourceProblem]]:
        grouped={}
        for m in members:grouped.setdefault(m.archive,[]).append(m)
        result={};problems=[]
        for archive,group in grouped.items():
            try:before=os.stat(archive)
            except OSError as e:
                problems.append(SourceProblem(archive,None,'archive_unavailable',str(e)));continue
            try:
                try:z=zipfile.ZipFile(archive)
                except Exception as e:
                    problems.append(SourceProblem(archive,None,'invalid_zip',str(e)));continue
                with z:
                    for m in group:
                        try:
                            with z.open(m.open_name) as f:raw=f.read()
                            result[(archive,m.path)]=json.loads(raw.decode('utf-8-sig'))
                        except Exception as e:
                            problems.append(SourceProblem(archive,m.path,'invalid_json',str(e)))
            finally:
                try:after=os.stat(archive)
                except OSError as e:
                    problems.append(SourceProblem(archive,None,'archive_unavailable',str(e)));continue
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    problems.append(SourceProblem(archive,None,'archive_changed','archive changed during JSON parse'))
        return result,problems

    def batch_sha256_tolerant(self,members:Iterable[Member])->tuple[dict[tuple[str,str],str],list[SourceProblem]]:
        grouped={}
        for m in members:grouped.setdefault(m.archive,[]).append(m)
        result={};problems=[]
        for archive,group in grouped.items():
            try:before=os.stat(archive)
            except OSError as e:
                problems.append(SourceProblem(archive,None,'archive_unavailable',str(e)));continue
            try:
                try:z=zipfile.ZipFile(archive)
                except Exception as e:
                    problems.append(SourceProblem(archive,None,'invalid_zip',str(e)));continue
                with z:
                    for m in group:
                        try:
                            h=hashlib.sha256()
                            with z.open(m.open_name) as f:
                                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
                            result[(archive,m.path)]=h.hexdigest()
                        except Exception as e:
                            problems.append(SourceProblem(archive,m.path,'media_read_error',str(e)))
            finally:
                try:after=os.stat(archive)
                except OSError as e:
                    problems.append(SourceProblem(archive,None,'archive_unavailable',str(e)));continue
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    problems.append(SourceProblem(archive,None,'archive_changed','archive changed during hashing'))
        return result,problems

    def batch_json(self, members: Iterable[Member]) -> dict[tuple[str,str], dict]:
        grouped: dict[str,list[Member]]={}
        for m in members: grouped.setdefault(m.archive,[]).append(m)
        result={}
        for archive, group in grouped.items():
            before=os.stat(archive)
            try:
                with zipfile.ZipFile(archive) as z:
                    for m in group:
                        try:
                            with z.open(m.open_name) as f:
                                raw=f.read()
                            result[(archive,m.path)]=json.loads(raw.decode("utf-8-sig"))
                        except Exception as e:
                            raise SourceIntegrityError(f"invalid JSON {archive}:{m.path}") from e
            finally:
                after=os.stat(archive)
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    raise SourceIntegrityError(f"archive changed during JSON parse: {archive}")
        return result

    def batch_sha256(self, members: Iterable[Member]) -> dict[tuple[str,str], str]:
        grouped: dict[str,list[Member]]={}
        for m in members: grouped.setdefault(m.archive,[]).append(m)
        result={}
        for archive, group in grouped.items():
            before=os.stat(archive)
            try:
                with zipfile.ZipFile(archive) as z:
                    for m in group:
                        h=hashlib.sha256()
                        try:
                            with z.open(m.open_name) as f:
                                for chunk in iter(lambda:f.read(1024*1024), b""):
                                    h.update(chunk)
                        except Exception as e:
                            raise SourceIntegrityError(f"failed hashing {archive}:{m.path}") from e
                        result[(archive,m.path)]=h.hexdigest()
            finally:
                after=os.stat(archive)
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    raise SourceIntegrityError(f"archive changed during processing: {archive}")
        return result
