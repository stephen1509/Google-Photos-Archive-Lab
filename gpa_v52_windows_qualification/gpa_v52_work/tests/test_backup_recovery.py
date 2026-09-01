from pathlib import Path
import subprocess,sys
import zipfile


def test_source_vault_crash_after_byte_commit_resumes_without_duplicate_bytes(tmp_path):
    from gpa.source_vault import vault_source_archive,audit_source_vault
    raw=tmp_path/'takeout.zip'
    with zipfile.ZipFile(raw,'w') as z:z.writestr('x.txt',b'x')
    vault=tmp_path/'vault'
    def crash(_dest):raise RuntimeError('power loss')
    try:vault_source_archive(raw,vault,after_bytes_commit=crash)
    except RuntimeError:pass
    else:raise AssertionError('crash hook did not fire')
    objects=list((vault/'archives').rglob('*.zip'));assert len(objects)==1
    assert not audit_source_vault(vault).ok
    row=vault_source_archive(raw,vault);assert row.reused_existing_bytes
    audit=audit_source_vault(vault);assert audit.ok and audit.archives_checked==1 and audit.records_checked==1
    assert list((vault/'archives').rglob('*.zip'))==objects


def test_mirror_sync_crash_resumes_no_overwrite(tmp_path):
    from gpa.mirror import build_tree_manifest,synchronize_mirror,verify_mirror
    source=tmp_path/'source';mirror=tmp_path/'mirror'
    for i in range(8):
        p=source/f'd/{i}.bin';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(bytes([i])*2048)
    manifest=build_tree_manifest(source)
    def crash(count,_dest):
        if count==3:raise RuntimeError('power loss')
    try:synchronize_mirror(manifest,source,mirror,after_commit=crash)
    except RuntimeError:pass
    else:raise AssertionError('crash hook did not fire')
    partial=verify_mirror(manifest,mirror);assert not partial.ok and partial.files_checked==3 and len(partial.missing)==5
    before={p.relative_to(mirror).as_posix():p.read_bytes() for p in mirror.rglob('*') if p.is_file()}
    healed=synchronize_mirror(manifest,source,mirror);assert healed.ok and healed.files_checked==8
    assert all((mirror/k).read_bytes()==v for k,v in before.items())


def test_mirror_conflict_preflight_prevents_partial_mutation(tmp_path):
    from gpa.mirror import build_tree_manifest,synchronize_mirror,MirrorConflict
    source=tmp_path/'source';mirror=tmp_path/'mirror'
    for name,data in [('a',b'a'),('b',b'b')]:
        p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    manifest=build_tree_manifest(source);(mirror/'a').parent.mkdir(parents=True,exist_ok=True);(mirror/'a').write_bytes(b'bad')
    try:synchronize_mirror(manifest,source,mirror)
    except MirrorConflict:pass
    else:raise AssertionError('conflicting destination was not rejected')
    assert not (mirror/'b').exists()


def test_mirror_manifest_path_traversal_fails_closed(tmp_path):
    from gpa.mirror import synchronize_mirror,verify_mirror
    manifest={'schema':'gpa.tree-manifest.v1','files':[{'path':'../escape','size':1,'sha256':'0'*64}]}
    report=verify_mirror(manifest,tmp_path/'mirror');assert not report.ok and report.invalid_manifest
    try:synchronize_mirror(manifest,tmp_path/'source',tmp_path/'mirror')
    except ValueError:pass
    else:raise AssertionError('unsafe path was accepted')
    assert not (tmp_path/'escape').exists()


def test_backup_fault_recovery_stress_tool_smoke(tmp_path):
    script=Path(__file__).parents[1]/'tools'/'stress_backup_recovery.py'
    r=subprocess.run([sys.executable,str(script),'--workdir',str(tmp_path/'lab'),'--sources','2','--archive-files','6'],capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stdout+r.stderr
    assert '"passed": true' in r.stdout


def _symlink_or_skip(target, link):
    import pytest
    try:
        link.symlink_to(target, target_is_directory=Path(target).is_dir())
    except (OSError, NotImplementedError) as e:
        pytest.skip(f'symlinks unavailable in this test environment: {e}')


def test_tree_manifest_rejects_symlinked_source_file(tmp_path):
    import pytest
    from gpa.mirror import build_tree_manifest
    root=tmp_path/'root';root.mkdir();outside=tmp_path/'outside.bin';outside.write_bytes(b'outside')
    _symlink_or_skip(outside,root/'linked.bin')
    with pytest.raises(RuntimeError,match='symlink|junction'):
        build_tree_manifest(root)


def test_mirror_verification_rejects_matching_symlink_destination(tmp_path):
    from gpa.mirror import build_tree_manifest,verify_mirror
    source=tmp_path/'source';source.mkdir();(source/'a.bin').write_bytes(b'correct bytes')
    manifest=build_tree_manifest(source);mirror=tmp_path/'mirror';mirror.mkdir()
    _symlink_or_skip(source/'a.bin',mirror/'a.bin')
    report=verify_mirror(manifest,mirror)
    assert not report.ok and report.unsafe_paths
    assert report.files_checked==0


def test_mirror_sync_rejects_symlink_destination_before_copying_missing_files(tmp_path):
    import pytest
    from gpa.mirror import build_tree_manifest,synchronize_mirror,MirrorConflict
    source=tmp_path/'source';source.mkdir();(source/'a.bin').write_bytes(b'a');(source/'b.bin').write_bytes(b'b')
    manifest=build_tree_manifest(source);mirror=tmp_path/'mirror';mirror.mkdir()
    _symlink_or_skip(source/'a.bin',mirror/'a.bin')
    with pytest.raises(MirrorConflict,match='symlink|junction'):
        synchronize_mirror(manifest,source,mirror)
    assert not (mirror/'b.bin').exists()


def test_source_vault_audit_detects_orphan_after_later_byte_commit_crash(tmp_path):
    import zipfile
    from gpa.source_vault import vault_source_archive,audit_source_vault
    vault=tmp_path/'vault';first=tmp_path/'first.zip';second=tmp_path/'second.zip'
    with zipfile.ZipFile(first,'w') as z:z.writestr('a.txt',b'a')
    with zipfile.ZipFile(second,'w') as z:z.writestr('b.txt',b'b')
    vault_source_archive(first,vault)
    def crash(_dest):raise RuntimeError('power loss')
    try:vault_source_archive(second,vault,after_bytes_commit=crash)
    except RuntimeError:pass
    else:raise AssertionError('crash hook did not fire')
    audit=audit_source_vault(vault)
    assert not audit.ok and any('orphan vaulted byte object' in p for p in audit.problems)
    vault_source_archive(second,vault)
    assert audit_source_vault(vault).ok


def test_source_vault_audit_rejects_conflicting_duplicate_record_binding(tmp_path):
    import json,zipfile
    from gpa.source_vault import vault_source_archive,audit_source_vault
    vault=tmp_path/'vault';raw=tmp_path/'takeout.zip'
    with zipfile.ZipFile(raw,'w') as z:z.writestr('a.txt',b'a')
    vaulted=vault_source_archive(raw,vault);record=vault/vaulted.record_path
    obj=json.loads(record.read_text(encoding='utf-8'));obj['sha256']='0'*64
    fake=record.with_name('fake.json');fake.write_text(json.dumps(obj),encoding='utf-8')
    audit=audit_source_vault(vault)
    assert not audit.ok and any('disagrees with verified byte-object binding' in p or 'hash/size mismatch' in p for p in audit.problems)
    assert '0'*64 not in audit.digests


def test_source_vault_audit_rejects_symlinked_archive_object(tmp_path):
    import json,zipfile
    from gpa.source_vault import vault_source_archive,audit_source_vault
    vault=tmp_path/'vault';raw=tmp_path/'takeout.zip'
    with zipfile.ZipFile(raw,'w') as z:z.writestr('a.txt',b'a')
    vaulted=vault_source_archive(raw,vault);record=vault/vaulted.record_path;obj=json.loads(record.read_text())
    real=vault/vaulted.vaulted_path;outside=tmp_path/'outside.zip';real.replace(outside)
    _symlink_or_skip(outside,real)
    audit=audit_source_vault(vault)
    assert not audit.ok and any('symlink/junction' in p for p in audit.problems)
