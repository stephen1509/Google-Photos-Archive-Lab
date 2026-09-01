from __future__ import annotations

import hashlib
import io
from pathlib import Path
import stat
import zipfile

import pytest

from gpa.exiftool_distribution import (
    ADMISSION_SCHEMA,
    EXIFTOOL_DISTRIBUTIONS,
    ExifToolDistributionError,
    ExifToolDistributionSpec,
    acquire_distribution_archive,
    admit_distribution,
    inspect_distribution_zip,
    prepare_distribution,
    verify_distribution_archive,
    distribution_tree_sha256,
)


def _zip_bytes(entries: list[tuple[str, bytes, int | None]]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            # ZipInfo normalizes native ``\\`` separators to ``/`` on Windows.
            # Preserve deliberately hostile test names so archive validation sees
            # the bytes the test is intended to exercise.
            info.filename = name
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode is not None:
                info.create_system = 3
                info.external_attr = mode << 16
            z.writestr(info, data)
    return b.getvalue()


def _spec(raw: bytes, *, top='exiftool-test') -> ExifToolDistributionSpec:
    return ExifToolDistributionSpec(
        candidate_id='test-win-x64', version='99.1', platform='windows', architecture='x64',
        filename='candidate.zip', source_url='https://example.invalid/candidate.zip',
        size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), top_level_dir=top,
    )


def _good_zip() -> bytes:
    return _zip_bytes([
        ('exiftool-test/exiftool(-k).exe', b'EXE-BYTES', None),
        ('exiftool-test/exiftool_files/lib/x.pm', b'package x;', None),
        ('exiftool-test/README.txt', b'readme', None),
    ])


def test_real_candidate_manifests_are_exactly_pinned():
    a = EXIFTOOL_DISTRIBUTIONS['13.55-win-x64']
    b = EXIFTOOL_DISTRIBUTIONS['13.59-win-x64']
    assert (a.size_bytes, a.sha256) == (11_117_157, '9fadaf5221dcb07a5d018e21c8529e257917d2fad1fdfc8e64855c4fb73293df')
    assert (b.size_bytes, b.sha256) == (11_183_675, '44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec')
    assert a.source_url.startswith('https://download.sourceforge.net/')


def test_exact_archive_verifies_and_wrong_bytes_fail(tmp_path):
    raw = _good_zip(); spec = _spec(raw); p = tmp_path/'candidate.zip'; p.write_bytes(raw)
    assert verify_distribution_archive(p, spec) == (len(raw), hashlib.sha256(raw).hexdigest())
    p.write_bytes(raw + b'x')
    with pytest.raises(ExifToolDistributionError, match='size mismatch'):
        verify_distribution_archive(p, spec)


def test_safe_prepare_renames_executable_without_changing_bytes(tmp_path):
    raw = _good_zip(); spec = _spec(raw); archive = tmp_path/'candidate.zip'; archive.write_bytes(raw)
    root, exe, members, total = prepare_distribution(archive, tmp_path/'prepared', spec)
    assert root.name == 'exiftool-test' and exe.name == 'exiftool.exe'
    assert exe.read_bytes() == b'EXE-BYTES'
    assert not (root/'exiftool(-k).exe').exists()
    assert (root/'exiftool_files/lib/x.pm').read_bytes() == b'package x;'
    assert members == 3 and total > 0


def test_admission_report_binds_archive_and_prepared_executable(tmp_path):
    raw = _good_zip(); spec = _spec(raw); archive = tmp_path/'candidate.zip'; archive.write_bytes(raw)
    r = admit_distribution(spec, archive, tmp_path/'prepared')
    assert r.schema == ADMISSION_SCHEMA and r.archive_verified and not r.errors
    assert r.checks['size_matches'] and r.checks['sha256_matches'] and r.checks['zip_structure_safe']
    assert r.checks['distribution_prepared'] and r.checks['executable_present']
    assert r.executable_sha256 == hashlib.sha256(b'EXE-BYTES').hexdigest()
    assert r.prepared_distribution_sha256 == distribution_tree_sha256(Path(r.prepared_distribution_root))


@pytest.mark.parametrize('bad_name', [
    '../escape.exe', '/absolute.exe', 'exiftool-test/../escape.exe', 'exiftool-test\\evil.exe',
])
def test_zip_path_traversal_and_windows_separator_are_refused(tmp_path, bad_name):
    raw = _zip_bytes([(bad_name, b'x', None)])
    spec = _spec(raw); p = tmp_path/'candidate.zip'; p.write_bytes(raw)
    with pytest.raises(ExifToolDistributionError, match='unsafe|outside'):
        inspect_distribution_zip(p, spec)


def test_zip_symlink_is_refused(tmp_path):
    raw = _zip_bytes([('exiftool-test/exiftool(-k).exe', b'target', stat.S_IFLNK | 0o777)])
    spec = _spec(raw); p = tmp_path/'candidate.zip'; p.write_bytes(raw)
    with pytest.raises(ExifToolDistributionError, match='symlink'):
        inspect_distribution_zip(p, spec)


def test_zip_case_collision_is_refused_before_extraction(tmp_path):
    raw = _zip_bytes([
        ('exiftool-test/A.txt', b'a', None), ('exiftool-test/a.TXT', b'b', None),
    ])
    spec = _spec(raw); p = tmp_path/'candidate.zip'; p.write_bytes(raw)
    with pytest.raises(ExifToolDistributionError, match='collision'):
        inspect_distribution_zip(p, spec)


def test_existing_exact_download_cache_is_reused_without_network(tmp_path, monkeypatch):
    raw = _good_zip(); spec = _spec(raw); cache = tmp_path/'cache'; cache.mkdir(); (cache/spec.filename).write_bytes(raw)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('network used')))
    p, downloaded, reused, final_url = acquire_distribution_archive(spec, cache)
    assert p.read_bytes() == raw and not downloaded and reused and final_url is None


def test_download_stream_is_hash_verified_before_commit(tmp_path, monkeypatch):
    raw = _good_zip(); spec = _spec(raw)
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close()
        def geturl(self): return 'https://mirror.example/candidate.zip'
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: Response(raw))
    p, downloaded, reused, final_url = acquire_distribution_archive(spec, tmp_path/'cache')
    assert p.read_bytes() == raw and downloaded and not reused
    assert final_url == 'https://mirror.example/candidate.zip'
    assert not list((tmp_path/'cache').glob('.gpa-exiftool-download-*'))


def test_bad_download_never_commits_candidate(tmp_path, monkeypatch):
    raw = _good_zip(); spec = _spec(raw)
    bad = bytearray(raw); bad[-1] ^= 1
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close()
        def geturl(self): return 'https://mirror.example/candidate.zip'
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: Response(bytes(bad)))
    with pytest.raises(ExifToolDistributionError, match='SHA-256 mismatch'):
        acquire_distribution_archive(spec, tmp_path/'cache')
    assert not (tmp_path/'cache'/spec.filename).exists()


@pytest.mark.parametrize('bad_name', [
    'exiftool-test/NUL.txt',
    'exiftool-test/COM1',
    'exiftool-test/evil:stream.txt',
    'exiftool-test/trailing. ',
    'exiftool-test/question?.txt',
])
def test_windows_unsafe_member_names_are_refused(tmp_path, bad_name):
    raw = _zip_bytes([(bad_name, b'x', None)])
    spec = _spec(raw); p = tmp_path/'candidate.zip'; p.write_bytes(raw)
    with pytest.raises(ExifToolDistributionError, match='Windows|unsafe'):
        inspect_distribution_zip(p, spec)


def test_zip_special_file_is_refused(tmp_path):
    raw = _zip_bytes([('exiftool-test/device', b'x', stat.S_IFIFO | 0o600)])
    spec = _spec(raw); p = tmp_path/'candidate.zip'; p.write_bytes(raw)
    with pytest.raises(ExifToolDistributionError, match='special-file'):
        inspect_distribution_zip(p, spec)


def test_incomplete_distribution_is_removed_on_prepare_failure(tmp_path):
    raw = _zip_bytes([('exiftool-test/exiftool(-k).exe', b'EXE-BYTES', None)])
    spec = _spec(raw); archive = tmp_path/'candidate.zip'; archive.write_bytes(raw)
    parent = tmp_path/'prepared'
    with pytest.raises(ExifToolDistributionError, match='missing exiftool_files'):
        prepare_distribution(archive, parent, spec)
    assert not (parent/spec.top_level_dir).exists()
    assert not list(parent.glob('.gpa-exiftool-extract-*'))



def test_cli_admit_exact_local_archive_writes_report_and_prepares(tmp_path, capsys):
    from gpa.__main__ import main
    raw = _good_zip(); spec = _spec(raw); archive = tmp_path/'candidate.zip'; archive.write_bytes(raw)
    # Temporarily inject the exact synthetic candidate into the pinned registry so
    # the CLI path is exercised without any network dependency.
    from gpa import exiftool_distribution as dist
    dist.EXIFTOOL_DISTRIBUTIONS[spec.candidate_id] = spec
    try:
        report = tmp_path/'admission.json'; prepared = tmp_path/'prepared'
        rc = main(['admit-exiftool','--candidate',spec.candidate_id,'--archive',str(archive),'--prepare',str(prepared),'--report',str(report)])
        assert rc == 0 and report.is_file()
        payload = __import__('json').loads(report.read_text())
        assert payload['archive_verified'] is True
        assert payload['checks']['distribution_prepared'] is True
        assert Path(payload['executable_path']).name == 'exiftool.exe'
        assert (prepared/spec.top_level_dir/'exiftool.exe').read_bytes() == b'EXE-BYTES'
    finally:
        dist.EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id, None)
    capsys.readouterr()
