from __future__ import annotations

"""Pinned external media fixtures used only by qualification harnesses.

A fixture is evidence input, never production approval.  The verifier deliberately
accepts only exact regular-file bytes matching the pinned manifest.  This keeps an
unrelated or locally modified HEIC from silently satisfying a format qualification.
"""

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import stat


FIXTURE_SCHEMA = "gpa.external-qualification-fixture.v1"
HEIC_FIXTURE_ID = "libheif-rainbow-451x461-heic-v1"


class QualificationFixtureError(RuntimeError):
    pass


@dataclass(frozen=True)
class QualificationFixtureSpec:
    fixture_id: str
    format_profile_id: str
    filename: str
    source_repository: str
    source_commit: str
    source_path: str
    source_git_blob_sha1: str
    source_url: str
    expected_size: int
    sha256: str
    license: str
    license_basis: str
    schema: str = FIXTURE_SCHEMA

    def to_dict(self) -> dict:
        return asdict(self)


HEIC_FIXTURE = QualificationFixtureSpec(
    fixture_id=HEIC_FIXTURE_ID,
    format_profile_id="heic-single-v1",
    filename="rainbow-451x461.heic",
    source_repository="strukturag/libheif",
    source_commit="1a3583bcce77de6d3f8701c0758e3954863681ba",
    source_path="tests/data/rainbow-451x461.heic",
    source_git_blob_sha1="6691f50f39bd69871a2abe284de2ef9f5243bc66",
    source_url=(
        "https://raw.githubusercontent.com/strukturag/libheif/"
        "1a3583bcce77de6d3f8701c0758e3954863681ba/tests/data/rainbow-451x461.heic"
    ),
    expected_size=7080,
    sha256="4b2ce727f093944975f143ba2b39c4c64511b766d94552f8d51a755916e7f983",
    license="LGPL-3.0",
    license_basis=(
        "libheif repository COPYING declares libheif under LGPL; codec-corpus HEIC "
        "conformance documentation classifies libheif test data as LGPL-3.0."
    ),
)

FIXTURES = {HEIC_FIXTURE.fixture_id: HEIC_FIXTURE}
FIXTURE_BY_PROFILE = {HEIC_FIXTURE.format_profile_id: HEIC_FIXTURE}


def fixture_for_profile(format_profile_id: str) -> QualificationFixtureSpec | None:
    return FIXTURE_BY_PROFILE.get(str(format_profile_id))


def fixture_spec(fixture_id: str) -> QualificationFixtureSpec:
    try:
        return FIXTURES[str(fixture_id)]
    except KeyError as e:
        raise QualificationFixtureError(f"unknown qualification fixture: {fixture_id}") from e


def verify_qualification_fixture(path: Path, spec: QualificationFixtureSpec) -> dict:
    """Verify exact pinned bytes and return machine-readable admission evidence."""
    path = Path(path)
    try:
        st = path.lstat()
    except FileNotFoundError as e:
        raise QualificationFixtureError(f"qualification fixture does not exist: {path}") from e
    if stat.S_ISLNK(st.st_mode):
        raise QualificationFixtureError("qualification fixture must not be a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise QualificationFixtureError("qualification fixture must be a regular file")
    if st.st_size != spec.expected_size:
        raise QualificationFixtureError(
            f"qualification fixture size mismatch: expected {spec.expected_size}, observed {st.st_size}"
        )
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest.casefold() != spec.sha256.casefold():
        raise QualificationFixtureError(
            f"qualification fixture SHA-256 mismatch: expected {spec.sha256}, observed {digest}"
        )
    git_blob = hashlib.sha1(f'blob {len(data)}\0'.encode('ascii') + data).hexdigest()
    if git_blob.casefold() != spec.source_git_blob_sha1.casefold():
        raise QualificationFixtureError(
            f"qualification fixture Git blob mismatch: expected {spec.source_git_blob_sha1}, observed {git_blob}"
        )
    return {
        "schema": FIXTURE_SCHEMA,
        "fixture_id": spec.fixture_id,
        "format_profile_id": spec.format_profile_id,
        "path": str(path.resolve()),
        "filename": spec.filename,
        "source_repository": spec.source_repository,
        "source_commit": spec.source_commit,
        "source_path": spec.source_path,
        "source_git_blob_sha1": spec.source_git_blob_sha1,
        "observed_git_blob_sha1": git_blob,
        "source_git_blob_verified": True,
        "source_url": spec.source_url,
        "expected_size": spec.expected_size,
        "observed_size": st.st_size,
        "expected_sha256": spec.sha256,
        "observed_sha256": digest,
        "license": spec.license,
        "license_basis": spec.license_basis,
        "exact_bytes_verified": True,
        "production_write_approved": False,
    }


def acquire_qualification_fixture(
    spec: QualificationFixtureSpec,
    cache_dir: Path,
    *,
    timeout_seconds: float = 120.0,
) -> tuple[Path, bool, bool, str | None]:
    """Acquire an exact pinned qualification fixture without overwriting conflicts.

    Returns ``(path, downloaded, reused_existing, final_url)``.  Existing exact
    bytes are reused.  Existing conflicting bytes, insecure redirects, truncated
    or oversized downloads, and any provenance mismatch fail closed.
    """
    import os
    import tempfile
    import urllib.request
    from urllib.parse import urlparse

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    if urlparse(spec.source_url).scheme.casefold() != "https":
        raise QualificationFixtureError("qualification fixture source URL must use HTTPS")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        raise QualificationFixtureError("qualification fixture cache must be a real directory")
    dest = cache_dir / spec.filename
    if dest.exists() or dest.is_symlink():
        verify_qualification_fixture(dest, spec)
        return dest, False, True, None

    fd, raw = tempfile.mkstemp(prefix=".gpa-qualification-fixture-", dir=cache_dir)
    os.close(fd)
    staged = Path(raw)
    total = 0
    final_url: str | None = None
    try:
        with urllib.request.urlopen(spec.source_url, timeout=timeout_seconds) as response, staged.open("wb") as out:
            final_url = response.geturl()
            if urlparse(final_url).scheme.casefold() != "https":
                raise QualificationFixtureError("qualification fixture redirect left HTTPS")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > spec.expected_size:
                    raise QualificationFixtureError(
                        f"qualification fixture download exceeded pinned size ({total} > {spec.expected_size})"
                    )
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if total != spec.expected_size:
            raise QualificationFixtureError(
                f"qualification fixture download size mismatch: expected {spec.expected_size}, observed {total}"
            )
        verify_qualification_fixture(staged, spec)
        # Import locally to avoid adding a production mutation dependency at module import time.
        from .transactions import commit_no_overwrite, fsync_dir
        commit_no_overwrite(staged, dest)
        fsync_dir(cache_dir)
        verify_qualification_fixture(dest, spec)
        return dest, True, False, final_url
    except Exception:
        staged.unlink(missing_ok=True)
        raise
