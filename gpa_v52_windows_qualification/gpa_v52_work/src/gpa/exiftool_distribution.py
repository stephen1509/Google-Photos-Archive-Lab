from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import unicodedata
import urllib.request
import zipfile

from .transactions import commit_no_overwrite, fsync_dir, sha256_file

ADMISSION_SCHEMA = 'gpa.exiftool-distribution-admission.v1'
MAX_ZIP_MEMBERS = 5000
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_MEMBER_NAME_CHARS = 1024
MAX_COMPONENT_CHARS = 255
WINDOWS_INVALID_CHARS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_BASENAMES = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    | {f'COM{i}' for i in range(1, 10)}
    | {f'LPT{i}' for i in range(1, 10)}
)


class ExifToolDistributionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExifToolDistributionSpec:
    candidate_id: str
    version: str
    platform: str
    architecture: str
    filename: str
    source_url: str
    size_bytes: int
    sha256: str
    top_level_dir: str
    executable_archive_name: str = 'exiftool(-k).exe'
    executable_runtime_name: str = 'exiftool.exe'


# Pinned against independently corroborated distribution metadata.  These are
# candidate manifests only; inclusion here is NOT production writer approval.
EXIFTOOL_DISTRIBUTIONS = {
    '13.55-win-x64': ExifToolDistributionSpec(
        candidate_id='13.55-win-x64',
        version='13.55',
        platform='windows',
        architecture='x64',
        filename='exiftool-13.55_64.zip',
        source_url='https://download.sourceforge.net/project/exiftool/files/exiftool-13.55_64.zip',
        size_bytes=11_117_157,
        sha256='9fadaf5221dcb07a5d018e21c8529e257917d2fad1fdfc8e64855c4fb73293df',
        top_level_dir='exiftool-13.55_64',
    ),
    '13.59-win-x64': ExifToolDistributionSpec(
        candidate_id='13.59-win-x64',
        version='13.59',
        platform='windows',
        architecture='x64',
        filename='exiftool-13.59_64.zip',
        source_url='https://sourceforge.net/projects/exiftool/files/exiftool-13.59_64.zip/download',
        size_bytes=11_183_675,
        sha256='44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec',
        top_level_dir='exiftool-13.59_64',
    ),
}


@dataclass
class ExifToolDistributionAdmissionReport:
    schema: str = ADMISSION_SCHEMA
    created_utc: str = ''
    candidate_id: str = ''
    version: str = ''
    platform: str = ''
    architecture: str = ''
    source_url: str = ''
    final_url: str | None = None
    archive_path: str = ''
    expected_size_bytes: int = 0
    observed_size_bytes: int | None = None
    expected_sha256: str = ''
    observed_sha256: str | None = None
    archive_verified: bool = False
    downloaded: bool = False
    reused_existing: bool = False
    zip_members: int | None = None
    zip_uncompressed_bytes: int | None = None
    prepared_distribution_root: str | None = None
    executable_path: str | None = None
    executable_sha256: str | None = None
    prepared_distribution_sha256: str | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def get_distribution_spec(candidate_id: str) -> ExifToolDistributionSpec:
    try:
        return EXIFTOOL_DISTRIBUTIONS[candidate_id]
    except KeyError as e:
        raise ExifToolDistributionError(
            f'unknown ExifTool candidate {candidate_id!r}; pinned candidates: {sorted(EXIFTOOL_DISTRIBUTIONS)}'
        ) from e


def _hash_size(path: Path) -> tuple[int, str]:
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ExifToolDistributionError(f'candidate archive must be a regular non-symlink file: {p}')
    return p.stat().st_size, sha256_file(p)


def _is_linkish(path: Path) -> bool:
    p = Path(path)
    try:
        if p.is_symlink():
            return True
        is_junction = getattr(p, 'is_junction', None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def distribution_tree_sha256(root: Path) -> str:
    """Fingerprint every regular file in a prepared ExifTool distribution.

    The digest binds normalized relative file paths, sizes and SHA-256 values.
    Symlink/junction-like objects are refused so a candidate cannot borrow bytes
    from outside the declared distribution root. Empty directories are ignored.
    """
    root = Path(root).resolve()
    if not root.is_dir() or _is_linkish(root):
        raise ExifToolDistributionError(f'distribution root is not a safe directory: {root}')
    rows: list[tuple[str, int, str]] = []
    for p in sorted(root.rglob('*'), key=lambda x: x.relative_to(root).as_posix()):
        rel = p.relative_to(root).as_posix()
        if _is_linkish(p):
            raise ExifToolDistributionError(f'distribution fingerprint refuses symlink/junction-like object: {rel}')
        if p.is_dir():
            continue
        if not p.is_file():
            raise ExifToolDistributionError(f'distribution fingerprint refuses non-regular object: {rel}')
        rows.append((rel, p.stat().st_size, sha256_file(p)))
    h = hashlib.sha256()
    for rel, size, digest in rows:
        h.update(rel.encode('utf-8')); h.update(b'\0')
        h.update(str(size).encode('ascii')); h.update(b'\0')
        h.update(digest.encode('ascii')); h.update(b'\n')
    return h.hexdigest()


def verify_distribution_archive(path: Path, spec: ExifToolDistributionSpec) -> tuple[int, str]:
    size, digest = _hash_size(path)
    if size != spec.size_bytes:
        raise ExifToolDistributionError(
            f'ExifTool archive size mismatch for {spec.candidate_id}: expected {spec.size_bytes}, got {size}'
        )
    if digest.lower() != spec.sha256.lower():
        raise ExifToolDistributionError(
            f'ExifTool archive SHA-256 mismatch for {spec.candidate_id}: expected {spec.sha256}, got {digest}'
        )
    return size, digest


def _stream_download(url: str, staged: Path, expected_size: int, timeout_seconds: float) -> str:
    if timeout_seconds <= 0:
        raise ValueError('timeout_seconds must be > 0')
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response, staged.open('wb') as out:
            final_url = response.geturl()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise ExifToolDistributionError(
                        f'download exceeded pinned size ({total} > {expected_size}); refusing candidate'
                    )
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    if total != expected_size:
        staged.unlink(missing_ok=True)
        raise ExifToolDistributionError(
            f'download size mismatch: expected {expected_size}, got {total}'
        )
    return final_url


def acquire_distribution_archive(
    spec: ExifToolDistributionSpec,
    cache_dir: Path,
    *,
    timeout_seconds: float = 120.0,
) -> tuple[Path, bool, bool, str | None]:
    """Acquire a pinned distribution without ever accepting unverified bytes.

    Returns (archive_path, downloaded, reused_existing, final_url).  Existing
    exact bytes are reused.  An existing conflicting file blocks the operation.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / spec.filename
    if dest.exists() or dest.is_symlink():
        verify_distribution_archive(dest, spec)
        return dest, False, True, None

    fd, raw = tempfile.mkstemp(prefix='.gpa-exiftool-download-', dir=cache_dir)
    os.close(fd)
    staged = Path(raw)
    try:
        final_url = _stream_download(spec.source_url, staged, spec.size_bytes, timeout_seconds)
        verify_distribution_archive(staged, spec)
        commit_no_overwrite(staged, dest)
        verify_distribution_archive(dest, spec)
        return dest, True, False, final_url
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def _validate_windows_component(part: str, *, member_name: str) -> None:
    if not part or part in ('.', '..'):
        raise ExifToolDistributionError(f'unsafe ZIP member path: {member_name!r}')
    if len(part) > MAX_COMPONENT_CHARS:
        raise ExifToolDistributionError(f'ZIP member component too long for conservative Windows extraction: {part!r}')
    if part[-1] in (' ', '.'):
        raise ExifToolDistributionError(f'ZIP member has Windows-ambiguous trailing space/dot: {part!r}')
    if any(ord(ch) < 32 or ch in WINDOWS_INVALID_CHARS for ch in part):
        raise ExifToolDistributionError(f'ZIP member contains a Windows-invalid filename character: {part!r}')
    # Win32 reserves these names even when an extension is appended (eg NUL.txt).
    stem = part.split('.', 1)[0].upper()
    if stem in WINDOWS_RESERVED_BASENAMES:
        raise ExifToolDistributionError(f'ZIP member uses a reserved Windows device name: {part!r}')


def _safe_member_name(name: str, spec: ExifToolDistributionSpec) -> tuple[PurePosixPath, str]:
    if not name or len(name) > MAX_MEMBER_NAME_CHARS or '\x00' in name or '\\' in name:
        raise ExifToolDistributionError(f'unsafe ZIP member name: {name!r}')
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in ('', '.', '..') for part in p.parts):
        raise ExifToolDistributionError(f'unsafe ZIP member path: {name!r}')
    if not p.parts or p.parts[0] != spec.top_level_dir:
        raise ExifToolDistributionError(
            f'ZIP member outside pinned top-level directory {spec.top_level_dir!r}: {name!r}'
        )
    for part in p.parts:
        _validate_windows_component(part, member_name=name)
    # Windows destination names are case-insensitive and Unicode can have
    # multiple spellings. Normalize/case-fold to detect collisions before I/O.
    collision_key = '/'.join(unicodedata.normalize('NFC', x).casefold() for x in p.parts)
    return p, collision_key


def inspect_distribution_zip(path: Path, spec: ExifToolDistributionSpec) -> tuple[list[zipfile.ZipInfo], int]:
    verify_distribution_archive(path, spec)
    try:
        zf = zipfile.ZipFile(path, 'r')
    except zipfile.BadZipFile as e:
        raise ExifToolDistributionError('pinned ExifTool archive is not a valid ZIP') from e
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_MEMBERS:
            raise ExifToolDistributionError(f'ZIP has too many members: {len(infos)} > {MAX_ZIP_MEMBERS}')
        seen: set[str] = set()
        total = 0
        for info in infos:
            # ``zipfile`` normalizes native separators in ``filename`` on
            # Windows. Validate the raw central-directory name instead so an
            # archive cannot hide a backslash path separator during extraction.
            member_name = getattr(info, 'orig_filename', info.filename)
            _, key = _safe_member_name(member_name, spec)
            if key in seen:
                raise ExifToolDistributionError(f'ZIP contains a Windows/Unicode path collision: {info.filename!r}')
            seen.add(key)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ExifToolDistributionError(f'ZIP symlink member refused: {info.filename!r}')
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ExifToolDistributionError(f'ZIP special-file member refused: {info.filename!r}')
            if info.flag_bits & 0x1:
                raise ExifToolDistributionError(f'encrypted ZIP member refused: {info.filename!r}')
            total += int(info.file_size)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ExifToolDistributionError(
                    f'ZIP uncompressed size exceeds safety limit: {total} > {MAX_UNCOMPRESSED_BYTES}'
                )
        return infos, total


def prepare_distribution(
    archive: Path,
    destination_parent: Path,
    spec: ExifToolDistributionSpec,
) -> tuple[Path, Path, int, int]:
    """Safely extract an exact pinned Windows distribution into a new directory.

    `destination_parent/spec.top_level_dir` must not already exist.  The official
    `exiftool(-k).exe` is renamed (not rewritten) to `exiftool.exe` as documented
    for command-line use.  The executable bytes therefore remain identical.
    """
    infos, total = inspect_distribution_zip(archive, spec)
    # Use one absolute namespace for the staging directory and atomic commit.
    # A relative destination makes os.rename resolve the source and target
    # differently on Windows, despite both appearing under the same parent.
    destination_parent = Path(destination_parent).resolve()
    destination_parent.mkdir(parents=True, exist_ok=True)
    root = destination_parent / spec.top_level_dir
    if root.exists() or root.is_symlink():
        raise FileExistsError(root)

    # Extract first to a private staging directory.  Only the verified archive
    # can reach here; explicit member-by-member writes avoid ZipFile.extractall.
    stage = Path(tempfile.mkdtemp(prefix='.gpa-exiftool-extract-', dir=destination_parent))
    try:
        with zipfile.ZipFile(archive, 'r') as zf:
            for info in infos:
                rel, _ = _safe_member_name(info.filename, spec)
                target = stage.joinpath(*rel.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                try:
                    with os.fdopen(fd, 'wb') as out, zf.open(info, 'r') as src:
                        shutil.copyfileobj(src, out, length=1024 * 1024)
                        out.flush(); os.fsync(out.fileno())
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
        staged_root = stage / spec.top_level_dir
        original_exe = staged_root / spec.executable_archive_name
        if not original_exe.is_file() or original_exe.is_symlink():
            raise ExifToolDistributionError(
                f'pinned executable member missing or unsafe: {spec.executable_archive_name}'
            )
        runtime_exe = staged_root / spec.executable_runtime_name
        if runtime_exe.exists():
            raise ExifToolDistributionError(f'unexpected runtime executable already exists: {runtime_exe.name}')
        exe_sha_before = sha256_file(original_exe)
        original_exe.rename(runtime_exe)
        if sha256_file(runtime_exe) != exe_sha_before:
            raise ExifToolDistributionError('ExifTool executable bytes changed during documented rename')
        # Refuse a structurally incomplete package.
        if not (staged_root / 'exiftool_files').is_dir():
            raise ExifToolDistributionError('ExifTool distribution is missing exiftool_files directory')
        # Namespace commit.  root was preflighted absent; on Windows rename is
        # no-overwrite.  On all platforms immediately verify result identity.
        os.rename(staged_root, root)
        fsync_dir(destination_parent)
        return root, root / spec.executable_runtime_name, len(infos), total
    except Exception:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def admit_distribution(
    spec: ExifToolDistributionSpec,
    archive: Path,
    destination_parent: Path | None = None,
    *,
    source_url: str | None = None,
    downloaded: bool = False,
    reused_existing: bool = False,
) -> ExifToolDistributionAdmissionReport:
    report = ExifToolDistributionAdmissionReport(
        created_utc=datetime.now(timezone.utc).isoformat(),
        candidate_id=spec.candidate_id,
        version=spec.version,
        platform=spec.platform,
        architecture=spec.architecture,
        source_url=spec.source_url,
        final_url=source_url,
        archive_path=str(Path(archive).resolve()),
        expected_size_bytes=spec.size_bytes,
        expected_sha256=spec.sha256,
        downloaded=downloaded,
        reused_existing=reused_existing,
    )
    try:
        size, digest = _hash_size(Path(archive))
        report.observed_size_bytes = size; report.observed_sha256 = digest
        report.checks['size_matches'] = size == spec.size_bytes
        report.checks['sha256_matches'] = digest.lower() == spec.sha256.lower()
        verify_distribution_archive(Path(archive), spec)
        report.archive_verified = True
        report.checks['archive_verified'] = True
        infos, total = inspect_distribution_zip(Path(archive), spec)
        report.zip_members = len(infos); report.zip_uncompressed_bytes = total
        report.checks['zip_structure_safe'] = True
        if destination_parent is not None:
            root, exe, members, uncompressed = prepare_distribution(Path(archive), destination_parent, spec)
            report.prepared_distribution_root = str(root.resolve())
            report.executable_path = str(exe.resolve())
            report.executable_sha256 = sha256_file(exe)
            report.prepared_distribution_sha256 = distribution_tree_sha256(root)
            report.zip_members = members; report.zip_uncompressed_bytes = uncompressed
            report.checks['distribution_prepared'] = True
            report.checks['executable_present'] = exe.is_file() and not exe.is_symlink()
    except Exception as e:
        report.errors.append(str(e))
        report.checks.setdefault('archive_verified', False)
        report.checks.setdefault('zip_structure_safe', False)
        if destination_parent is not None:
            report.checks.setdefault('distribution_prepared', False)
            report.checks.setdefault('executable_present', False)
    return report
