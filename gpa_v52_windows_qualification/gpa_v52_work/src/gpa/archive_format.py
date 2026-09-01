from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .transactions import write_json_new, sha256_file

ARCHIVE_FORMAT_ID = 'gpa.archive.v1'
MANIFEST_SCHEMA = 'gpa.archive-manifest.v1'
MANIFEST_RELATIVE_PATH = Path('metadata/archive_format.json')
SUPPORTED_ARCHIVE_FORMATS = frozenset({ARCHIVE_FORMAT_ID})


class ArchiveFormatError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveFormatState:
    status: str  # empty | supported | legacy_unversioned | foreign_nonempty | invalid_manifest | unsupported
    archive_format: str | None = None
    manifest_schema: str | None = None
    manifest_path: str = str(MANIFEST_RELATIVE_PATH).replace('\\', '/')
    detail: str = ''

    @property
    def supported(self) -> bool:
        return self.status == 'supported' and self.archive_format in SUPPORTED_ARCHIVE_FORMATS

    @property
    def can_initialize(self) -> bool:
        return self.status == 'empty'

    @property
    def can_execute_without_upgrade(self) -> bool:
        return self.status in {'empty', 'supported'}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ArchiveUpgradeResult:
    action: str  # created | upgraded | unchanged
    archive_format: str
    manifest_path: str
    pre_upgrade_tree_sha256: str | None = None
    pre_upgrade_file_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def archive_manifest_path(root: Path) -> Path:
    return Path(root) / MANIFEST_RELATIVE_PATH


def _recognizable_gpa_content(root: Path) -> bool:
    return any(
        p.exists()
        for p in (
            root / 'metadata' / 'assets',
            root / 'metadata' / 'runs',
            root / 'metadata' / 'unclassified',
            root / 'Photos',
            root / '_Needs Placement',
        )
    )


def _is_empty_root(root: Path) -> bool:
    if not root.exists():
        return True
    try:
        return next(root.iterdir(), None) is None
    except OSError:
        return False


def inspect_archive_format(root: Path) -> ArchiveFormatState:
    root = Path(root)
    mp = archive_manifest_path(root)
    if mp.is_symlink():
        return ArchiveFormatState('invalid_manifest', detail='archive manifest must be a regular file, not a symlink')
    if mp.exists():
        try:
            obj = json.loads(mp.read_text(encoding='utf-8'))
        except Exception as e:
            return ArchiveFormatState('invalid_manifest', detail=f'unreadable archive manifest: {e}')
        schema = obj.get('schema')
        fmt = obj.get('archive_format')
        if schema != MANIFEST_SCHEMA:
            return ArchiveFormatState('invalid_manifest', archive_format=fmt, manifest_schema=schema, detail=f'unsupported manifest schema: {schema!r}')
        if fmt not in SUPPORTED_ARCHIVE_FORMATS:
            return ArchiveFormatState('unsupported', archive_format=fmt, manifest_schema=schema, detail=f'unsupported archive format: {fmt!r}')
        return ArchiveFormatState('supported', archive_format=fmt, manifest_schema=schema)
    if _is_empty_root(root):
        return ArchiveFormatState('empty', detail='destination is empty and can be initialized')
    if _recognizable_gpa_content(root):
        return ArchiveFormatState('legacy_unversioned', detail='recognizable GPA archive content exists without an archive-format manifest')
    return ArchiveFormatState('foreign_nonempty', detail='destination is non-empty but is not a recognized versioned GPA archive')


def _tree_evidence(root: Path, *, exclude: set[Path] | None = None) -> tuple[int, str]:
    root = Path(root)
    exclude = {p.resolve() for p in (exclude or set())}
    rows = []
    if root.exists():
        for p in sorted((p for p in root.rglob('*') if p.is_file() or p.is_symlink()), key=lambda x: x.relative_to(root).as_posix()):
            if p.is_symlink():
                raise ArchiveFormatError(f'legacy archive upgrade refuses symlinked file: {p.relative_to(root).as_posix()}')
            try:
                resolved=p.resolve();resolved.relative_to(root.resolve())
                if resolved in exclude:
                    continue
            except ValueError as e:
                raise ArchiveFormatError(f'legacy archive file resolves outside root: {p.relative_to(root).as_posix()}') from e
            except OSError:
                pass
            rel = p.relative_to(root).as_posix()
            rows.append((rel, p.stat().st_size, sha256_file(p)))
    h = hashlib.sha256()
    for rel, size, digest in rows:
        h.update(rel.encode('utf-8')); h.update(b'\0')
        h.update(str(size).encode('ascii')); h.update(b'\0')
        h.update(digest.encode('ascii')); h.update(b'\n')
    return len(rows), h.hexdigest()


def _manifest(*, upgraded_from: str | None = None, evidence: tuple[int, str] | None = None) -> dict:
    obj = {
        'schema': MANIFEST_SCHEMA,
        'archive_format': ARCHIVE_FORMAT_ID,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'producer': {'name': 'google-photos-archive-lab', 'version': '0.0.2'},
    }
    if upgraded_from:
        obj['upgraded_from'] = upgraded_from
    if evidence is not None:
        count, digest = evidence
        obj['pre_upgrade_evidence'] = {
            'file_count': count,
            'tree_sha256': digest,
            'scope': 'all existing files before archive-format manifest creation',
        }
    return obj


def create_archive_manifest(root: Path) -> ArchiveUpgradeResult:
    root = Path(root)
    state = inspect_archive_format(root)
    if state.supported:
        return ArchiveUpgradeResult('unchanged', state.archive_format or ARCHIVE_FORMAT_ID, state.manifest_path)
    if state.status != 'empty':
        raise ArchiveFormatError(f'cannot initialize archive format: {state.status}: {state.detail}')
    mp = archive_manifest_path(root)
    write_json_new(mp, _manifest())
    return ArchiveUpgradeResult('created', ARCHIVE_FORMAT_ID, str(mp.relative_to(root)).replace('\\', '/'))


def upgrade_archive_format(root: Path) -> ArchiveUpgradeResult:
    """Explicit metadata-only upgrade for a recognizable pre-manifest GPA archive.

    Existing files are hashed before the new manifest is written.  The operation
    never rewrites or relocates an existing media/metadata file.
    """
    root = Path(root)
    state = inspect_archive_format(root)
    if state.supported:
        return ArchiveUpgradeResult('unchanged', state.archive_format or ARCHIVE_FORMAT_ID, state.manifest_path)
    if state.status == 'empty':
        return create_archive_manifest(root)
    if state.status != 'legacy_unversioned':
        raise ArchiveFormatError(f'archive-format upgrade refused: {state.status}: {state.detail}')
    mp = archive_manifest_path(root)
    evidence = _tree_evidence(root, exclude={mp})
    write_json_new(mp, _manifest(upgraded_from='legacy-unversioned', evidence=evidence))
    return ArchiveUpgradeResult('upgraded', ARCHIVE_FORMAT_ID, str(mp.relative_to(root)).replace('\\', '/'), evidence[1], evidence[0])


def ensure_archive_for_write(root: Path) -> ArchiveUpgradeResult:
    state = inspect_archive_format(root)
    if state.supported:
        return ArchiveUpgradeResult('unchanged', state.archive_format or ARCHIVE_FORMAT_ID, state.manifest_path)
    if state.status == 'empty':
        return create_archive_manifest(root)
    if state.status == 'legacy_unversioned':
        raise ArchiveFormatError('legacy unversioned GPA archive requires explicit archive-format upgrade before mutation')
    raise ArchiveFormatError(f'archive mutation refused: {state.status}: {state.detail}')
