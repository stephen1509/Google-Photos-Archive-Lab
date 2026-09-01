from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .transactions import sha256_file, write_json_new


PACK_SCHEMA = 'gpa.data-pack.v1'


@dataclass(frozen=True)
class DataPackFile:
    path: str
    size: int
    sha256: str


@dataclass
class DataPackManifest:
    schema: str = PACK_SCHEMA
    role: str = ''
    name: str = ''
    version: str = ''
    license: str = ''
    attribution: str = ''
    source: str = ''
    temporal_mode: str | None = None
    created_utc: str = ''
    files: list[DataPackFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self); d['files'] = [asdict(x) for x in self.files]; return d

    @property
    def identity(self) -> str:
        body = self.to_dict().copy(); body.pop('created_utc', None)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()[:16]
        return f'{self.role}:{self.name}:{self.version}:{digest}'


@dataclass
class DataPackVerification:
    ok: bool = True
    files_checked: int = 0
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    unsafe_paths: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _safe_file(root: Path, rel: str) -> Path | None:
    try:
        p = (root / rel).resolve(); p.relative_to(root.resolve()); return p
    except (ValueError, OSError):
        return None


def create_data_pack_manifest(root: Path, *, role: str, name: str, version: str, license: str,
                              attribution: str, source: str, temporal_mode: str | None = None,
                              exclude_names: set[str] | None = None) -> DataPackManifest:
    root = Path(root); exclude_names = set(exclude_names or {'manifest.json'})
    if not role or not name or not version or not license or not attribution or not source:
        raise ValueError('data-pack role/name/version/license/attribution/source are required')
    rows = []
    for p in sorted(x for x in root.rglob('*') if x.is_file() and x.name not in exclude_names):
        rel = p.relative_to(root).as_posix()
        rows.append(DataPackFile(rel, p.stat().st_size, sha256_file(p)))
    return DataPackManifest(role=role, name=name, version=version, license=license, attribution=attribution,
                            source=source, temporal_mode=temporal_mode,
                            created_utc=datetime.now(timezone.utc).isoformat(), files=rows)


def write_data_pack_manifest(path: Path, manifest: DataPackManifest) -> Path:
    path = Path(path)
    write_json_new(path, manifest.to_dict())
    return path


def load_data_pack_manifest(path: Path) -> DataPackManifest:
    obj = json.loads(Path(path).read_text(encoding='utf-8'))
    if obj.get('schema') != PACK_SCHEMA:
        raise ValueError(f'unsupported data-pack schema: {obj.get("schema")}')
    files = [DataPackFile(str(x['path']), int(x['size']), str(x['sha256'])) for x in obj.get('files') or []]
    return DataPackManifest(schema=obj['schema'], role=str(obj.get('role') or ''), name=str(obj.get('name') or ''),
                            version=str(obj.get('version') or ''), license=str(obj.get('license') or ''),
                            attribution=str(obj.get('attribution') or ''), source=str(obj.get('source') or ''),
                            temporal_mode=obj.get('temporal_mode'), created_utc=str(obj.get('created_utc') or ''), files=files)


def verify_data_pack(root: Path, manifest: DataPackManifest | Path, *, report_extras: bool = True) -> DataPackVerification:
    root = Path(root); m = load_data_pack_manifest(manifest) if isinstance(manifest, (str, Path)) else manifest
    out = DataPackVerification()
    expected = set()
    if not m.role or not m.name or not m.version or not m.license or not m.attribution or not m.source:
        out.problems.append('manifest is missing required provenance/license fields')
    for row in m.files:
        expected.add(row.path)
        p = _safe_file(root, row.path)
        if p is None:
            out.unsafe_paths.append(row.path); continue
        if not p.exists():
            out.missing.append(row.path); continue
        out.files_checked += 1
        if p.stat().st_size != row.size or sha256_file(p) != row.sha256:
            out.mismatched.append(row.path)
    if report_extras:
        for p in sorted(x for x in root.rglob('*') if x.is_file()):
            rel = p.relative_to(root).as_posix()
            if rel not in expected and p.name != 'manifest.json': out.extras.append(rel)
    out.ok = not (out.missing or out.mismatched or out.unsafe_paths or out.problems)
    return out
