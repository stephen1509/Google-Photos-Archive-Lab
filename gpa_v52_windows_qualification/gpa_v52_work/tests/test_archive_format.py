from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from gpa.archive_format import (
    ARCHIVE_FORMAT_ID,
    MANIFEST_SCHEMA,
    ArchiveFormatError,
    archive_manifest_path,
    create_archive_manifest,
    inspect_archive_format,
    upgrade_archive_format,
)
from gpa.executor import RunExecutor, RunError
from gpa.preview import build_run_preview
from gpa.audit import audit_archive
from gpa.verification import verify_migration


def _zip(path: Path, entries: dict[str, bytes]):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_empty_destination_is_detected_without_creating_state(tmp_path):
    root = tmp_path / 'archive'
    s = inspect_archive_format(root)
    assert s.status == 'empty' and not root.exists()


def test_preview_is_read_only_and_reports_empty_initializable_format(tmp_path):
    z = tmp_path / 'takeout.zip'; _zip(z, {'D/a.jpg': b'A'})
    root = tmp_path / 'archive'
    p = build_run_preview(RunExecutor([z], root, embedded_provider=lambda m,l: None, storage_reserve_fraction=0))
    assert p.archive_format_status == 'empty' and p.can_execute
    assert not archive_manifest_path(root).exists()


def test_first_execution_creates_frozen_v1_manifest(tmp_path):
    z = tmp_path / 'takeout.zip'; _zip(z, {'D/a.jpg': b'A'})
    root = tmp_path / 'archive'
    RunExecutor([z], root, embedded_provider=lambda m,l: None, storage_reserve_fraction=0).execute(run_id='r')
    obj = json.loads(archive_manifest_path(root).read_text())
    assert obj['schema'] == MANIFEST_SCHEMA and obj['archive_format'] == ARCHIVE_FORMAT_ID
    assert inspect_archive_format(root).supported


def test_supported_manifest_is_idempotent(tmp_path):
    root = tmp_path / 'archive'
    a = create_archive_manifest(root); before = archive_manifest_path(root).read_bytes()
    b = create_archive_manifest(root)
    assert a.action == 'created' and b.action == 'unchanged' and archive_manifest_path(root).read_bytes() == before


def test_unknown_future_format_fails_closed_before_run_mutation(tmp_path):
    z = tmp_path / 'takeout.zip'; _zip(z, {'D/a.jpg': b'A'})
    root = tmp_path / 'archive'; (root / 'metadata').mkdir(parents=True)
    archive_manifest_path(root).write_text(json.dumps({'schema': MANIFEST_SCHEMA, 'archive_format': 'gpa.archive.v99'}))
    ex = RunExecutor([z], root, embedded_provider=lambda m,l: None, storage_reserve_fraction=0)
    preview = build_run_preview(ex)
    assert not preview.can_execute and preview.archive_format_status == 'unsupported'
    with pytest.raises(RunError): ex.execute(run_id='r')
    assert not (root / 'metadata' / 'runs').exists()


def test_unknown_manifest_schema_fails_closed(tmp_path):
    root = tmp_path / 'archive'; (root / 'metadata').mkdir(parents=True)
    archive_manifest_path(root).write_text(json.dumps({'schema':'gpa.archive-manifest.v99','archive_format':ARCHIVE_FORMAT_ID}))
    st = inspect_archive_format(root)
    assert st.status == 'invalid_manifest' and not st.supported
    assert not audit_archive(root).ok


def test_foreign_nonempty_destination_is_not_claimed_as_gpa_archive(tmp_path):
    root = tmp_path / 'archive'; root.mkdir(); (root / 'unrelated.txt').write_text('mine')
    assert inspect_archive_format(root).status == 'foreign_nonempty'
    with pytest.raises(ArchiveFormatError): create_archive_manifest(root)


def test_recognizable_legacy_archive_requires_explicit_upgrade(tmp_path):
    root = tmp_path / 'archive'; media = root / 'Photos/2017/05 - May/a.jpg'; media.parent.mkdir(parents=True); media.write_bytes(b'ORIGINAL')
    assert inspect_archive_format(root).status == 'legacy_unversioned'
    z = tmp_path / 'takeout.zip'; _zip(z, {'D/b.jpg': b'B'})
    with pytest.raises(RunError): RunExecutor([z], root, embedded_provider=lambda m,l: None, storage_reserve_fraction=0).execute(run_id='r')
    assert media.read_bytes() == b'ORIGINAL'


def test_legacy_upgrade_is_metadata_only_and_records_preupgrade_tree_hash(tmp_path):
    root = tmp_path / 'archive'; media = root / 'Photos/2017/05 - May/a.jpg'; media.parent.mkdir(parents=True); media.write_bytes(b'ORIGINAL')
    meta = root / 'metadata/assets/x/revisions/r.json'; meta.parent.mkdir(parents=True); meta.write_text('{"legacy":true}')
    before = {p.relative_to(root).as_posix(): _sha(p) for p in root.rglob('*') if p.is_file()}
    r = upgrade_archive_format(root)
    after = {p.relative_to(root).as_posix(): _sha(p) for p in root.rglob('*') if p.is_file() and p != archive_manifest_path(root)}
    assert r.action == 'upgraded' and before == after
    obj = json.loads(archive_manifest_path(root).read_text())
    assert obj['upgraded_from'] == 'legacy-unversioned'
    assert obj['pre_upgrade_evidence']['file_count'] == len(before)
    assert len(obj['pre_upgrade_evidence']['tree_sha256']) == 64


def test_legacy_upgrade_is_idempotent(tmp_path):
    root = tmp_path / 'archive'; (root / 'metadata/assets').mkdir(parents=True)
    first = upgrade_archive_format(root); manifest = archive_manifest_path(root).read_bytes()
    second = upgrade_archive_format(root)
    assert first.action == 'upgraded' and second.action == 'unchanged' and archive_manifest_path(root).read_bytes() == manifest


def test_verification_retirement_gate_requires_supported_archive_format(tmp_path):
    root = tmp_path / 'archive'; (root / 'metadata/assets').mkdir(parents=True); (root / 'metadata/runs').mkdir(parents=True)
    (root / 'metadata/runs/r.json').write_text(json.dumps({'status':'complete','source_archives':[], 'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    v = verify_migration(root, all_takeout_parts_confirmed=True, redundant_copy_verified=True, require_source_vault=False, require_takeout_receipt=False, require_exit_evidence=False, require_independent_media=False, require_media_validation=False)
    assert not v.archive_format_supported and not v.google_retirement_ready
    assert any('archive-format manifest' in b for b in v.blockers)


def test_verification_can_explicitly_ignore_archive_format_for_legacy_analysis(tmp_path):
    root = tmp_path / 'archive'; (root / 'metadata/assets').mkdir(parents=True); (root / 'metadata/runs').mkdir(parents=True)
    (root / 'metadata/runs/r.json').write_text(json.dumps({'status':'complete','source_archives':[], 'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    v = verify_migration(root, require_archive_format=False, require_media_validation=False, require_source_vault=False, require_takeout_receipt=False, require_exit_evidence=False, require_independent_media=False)
    assert not any('archive-format manifest' in b for b in v.blockers)



def test_manifest_symlink_is_invalid_and_never_followed(tmp_path):
    root=tmp_path/'archive';(root/'metadata').mkdir(parents=True)
    outside=tmp_path/'outside.json';outside.write_text(json.dumps({'schema':MANIFEST_SCHEMA,'archive_format':ARCHIVE_FORMAT_ID}))
    try:
        archive_manifest_path(root).symlink_to(outside)
    except OSError as e:
        pytest.skip(f'Windows symlink privilege unavailable; cannot exercise real symlink rejection: {e}')
    st=inspect_archive_format(root)
    assert st.status=='invalid_manifest' and not st.supported


def test_legacy_upgrade_refuses_symlinked_files(tmp_path):
    root=tmp_path/'archive';(root/'Photos').mkdir(parents=True)
    outside=tmp_path/'outside.jpg';outside.write_bytes(b'outside')
    try:
        (root/'Photos'/'link.jpg').symlink_to(outside)
    except OSError as e:
        pytest.skip(f'Windows symlink privilege unavailable; cannot exercise real symlink rejection: {e}')
    with pytest.raises(ArchiveFormatError):upgrade_archive_format(root)
    assert not archive_manifest_path(root).exists()
