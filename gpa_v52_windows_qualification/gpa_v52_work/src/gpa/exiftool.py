from __future__ import annotations
import hashlib, json, subprocess, sys
from pathlib import Path
from .writepolicy import MetadataPlan

class ExifToolError(Exception):pass

# Production embedded writing remains fail-closed until exact builds are promoted.
PRODUCTION_APPROVED_BUILDS=frozenset()
# A build promotion is deliberately insufficient.  Each exact build/distribution
# must also receive explicit format (and where applicable relationship) approvals.
PRODUCTION_FORMAT_APPROVALS=frozenset()


def _is_python_script(path: str) -> bool:
    """Identify explicit Python fixtures without weakening normal executable launch."""
    candidate = Path(path)
    if candidate.suffix.casefold() == '.py':
        return True
    try:
        if not candidate.is_file():
            return False
        with candidate.open('rb') as source:
            header = source.read(128).lower()
        return header.startswith(b'#!') and b'python' in header
    except OSError:
        return False


def executable_command(executable: str | Path, *arguments: str) -> list[str]:
    """Build a Windows-compatible command for a real tool or Python fixture."""
    command = [str(executable), *arguments]
    return [sys.executable, *command] if _is_python_script(command[0]) else command

class ExifToolAdapter:
    def __init__(self,executable='exiftool',approved_versions=('13.59',),approved_builds=None,timeout_seconds:float=120.0):
        if timeout_seconds<=0:raise ValueError('ExifTool timeout_seconds must be > 0')
        self.executable=str(executable);self.approved=set(approved_versions);self.approved_builds=None if approved_builds is None else set(approved_builds);self.timeout_seconds=float(timeout_seconds);self._version=None;self._build_sha=None
    def _run(self,args:list[str]):
        command=executable_command(args[0], *args[1:])
        # POSIX can execute a shebang Python fixture directly; Windows cannot.
        # This compatibility path is intentionally limited to explicit .py tools,
        # leaving normal ExifTool executables and production approvals unchanged.
        try:return subprocess.run(command,capture_output=True,text=True,timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as e:raise ExifToolError(f'ExifTool timed out after {self.timeout_seconds:g}s') from e
    def version(self)->str:
        if self._version:return self._version
        cp=self._run([self.executable,'-ver'])
        if cp.returncode:raise ExifToolError(cp.stderr.strip() or 'ExifTool version check failed')
        v=cp.stdout.strip()
        if self.approved_builds is not None:
            sha=self.build_sha256()
            if (v,sha) not in self.approved_builds:raise ExifToolError(f'unapproved ExifTool build version={v} sha256={sha}')
        elif v not in self.approved:raise ExifToolError(f'untested ExifTool version {v}; approved: {sorted(self.approved)}')
        self._version=v;return v
    def build_sha256(self)->str:
        if self._build_sha:return self._build_sha
        p=Path(self.executable)
        if p.is_symlink() or not p.is_file():raise ExifToolError('ExifTool build fingerprint requires a regular non-symlink executable')
        h=hashlib.sha256()
        with p.open('rb') as f:
            for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
        self._build_sha=h.hexdigest();return self._build_sha

    def read(self,path:Path)->dict:
        self.version()
        cp=self._run([self.executable,'-j','-G1','-struct','-n',str(path)])
        if cp.returncode:raise ExifToolError(cp.stderr.strip() or 'ExifTool read failed')
        try:data=json.loads(cp.stdout)
        except json.JSONDecodeError as e:raise ExifToolError('invalid ExifTool JSON') from e
        if not isinstance(data,list) or len(data)!=1:raise ExifToolError('unexpected ExifTool JSON shape')
        return data[0]
    def write_args(self,path:Path,plan:MetadataPlan)->list[str]:
        self.version();args=[self.executable,'-overwrite_original']
        for k,v in sorted({**plan.exif,**plan.xmp}.items()):
            # GPA's canonical sidecar key is namespace-neutral; ExifTool's
            # Windows executable requires its fully qualified XMP group name.
            tag = 'XMP-dc:Description' if k == 'dc:description' else k
            args.append(f'-{tag}={v}')
        args.append(str(path))
        return args
    def write(self,path:Path,plan:MetadataPlan)->str:
        cp=self._run(self.write_args(path,plan))
        if cp.returncode:raise ExifToolError(cp.stderr.strip() or 'ExifTool write failed')
        # ExifTool 13.59 promotes certain dangerous XMP write problems to errors; still reject warnings conservatively.
        combined=(cp.stdout+'\n'+cp.stderr).strip()
        if 'Warning:' in combined or 'Error:' in combined:raise ExifToolError(combined)
        return combined


class ProductionExifToolAdapter(ExifToolAdapter):
    """ExifTool adapter with an additional exact format/relationship write gate.

    Callers must explicitly provide ``relationship_kind`` to ``write``.  There is no
    default because silently assuming ``standalone`` could turn a JPEG build approval
    into permission to modify a Motion Photo, or a MOV approval into permission to
    modify a Live Photo component.
    """
    def __init__(
        self,
        executable='exiftool',
        *,
        distribution_root:Path|str|None=None,
        approved_builds=PRODUCTION_APPROVED_BUILDS,
        format_approvals=PRODUCTION_FORMAT_APPROVALS,
        timeout_seconds:float=120.0,
    ):
        super().__init__(executable,approved_versions=(),approved_builds=approved_builds,timeout_seconds=timeout_seconds)
        self.distribution_root=None if distribution_root is None else Path(distribution_root)
        self.format_approvals=frozenset(format_approvals)
        self._distribution_sha=None

    def distribution_sha256(self)->str|None:
        if self.distribution_root is None:return None
        if self._distribution_sha:return self._distribution_sha
        from .exiftool_distribution import distribution_tree_sha256
        try:self._distribution_sha=distribution_tree_sha256(self.distribution_root)
        except Exception as e:raise ExifToolError(f'could not fingerprint prepared ExifTool distribution: {e}') from e
        return self._distribution_sha

    def write_authorization(self,path:Path,*,relationship_kind:str):
        from .format_qualification import authorize_format_write
        version=self.version();exe_sha=self.build_sha256();dist_sha=self.distribution_sha256()
        return authorize_format_write(
            candidate_version=version,executable_sha256=exe_sha,distribution_sha256=dist_sha,
            path=path,relationship_kind=relationship_kind,approvals=self.format_approvals,
        )

    def write(self,path:Path,plan:MetadataPlan,*,relationship_kind:str)->str:
        decision=self.write_authorization(path,relationship_kind=relationship_kind)
        if not decision.allowed:
            raise ExifToolError('production embedded write is not approved: '+'; '.join(decision.reasons))
        return super().write(path,plan)


def production_exiftool_adapter(executable='exiftool',*,distribution_root:Path|str|None=None)->ProductionExifToolAdapter:
    return ProductionExifToolAdapter(executable,distribution_root=distribution_root)
