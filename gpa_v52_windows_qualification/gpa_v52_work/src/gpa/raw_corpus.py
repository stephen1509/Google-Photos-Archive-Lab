from __future__ import annotations

"""Read-only representative Camera RAW corpus qualification.

This layer binds the isolated rawpy/LibRaw decoder-health probe to an exact, diverse
set of source bytes.  The corpus is *not* bundled with GPA.  A manifest records exact
SHA-256 identities and source provenance; qualification only reads local copies whose
bytes match that manifest.

Passing this lane proves that one exact decoder build can open and decode a
representative set without changing the source bytes.  It does not qualify metadata
rewriting and does not grant production write approval for any RAW family.
"""

from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import tempfile
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from .formats import RAW_SUFFIXES
from .raw_validation import RawProbeResult, probe_raw_isolated
from .transactions import commit_no_overwrite, sha256_file, write_json_new

RAW_CORPUS_MANIFEST_SCHEMA = "gpa.raw-corpus-manifest.v1"
RAW_CORPUS_QUALIFICATION_SCHEMA = "gpa.raw-corpus-qualification.v1"
RAW_CORPUS_FETCH_SCHEMA = "gpa.raw-corpus-fetch.v1"
RAW_CORPUS_POLICY_ID = "gpa.raw-corpus-diversity-policy.v1"

CANONICAL_MIN_SAMPLES = 10
CANONICAL_MIN_BRANDS = 8
CANONICAL_MIN_FORMAT_FAMILIES = 8


@dataclass(frozen=True)
class RawCorpusPolicy:
    min_samples: int = CANONICAL_MIN_SAMPLES
    min_brands: int = CANONICAL_MIN_BRANDS
    min_format_families: int = CANONICAL_MIN_FORMAT_FAMILIES
    policy_id: str = RAW_CORPUS_POLICY_ID

    def __post_init__(self) -> None:
        if min(self.min_samples, self.min_brands, self.min_format_families) < 1:
            raise ValueError("RAW corpus diversity thresholds must all be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawCorpusSample:
    sample_id: str
    brand: str
    model: str
    format_family: str
    relative_path: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawCorpusManifest:
    corpus_id: str
    source_catalog: str
    source_base_url: str
    source_index_url: str
    catalog_snapshot_date: str
    rights_note: str
    samples: tuple[RawCorpusSample, ...]
    schema: str = RAW_CORPUS_MANIFEST_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["samples"] = [s.to_dict() for s in self.samples]
        return out


@dataclass(frozen=True)
class RawCorpusSampleResult:
    sample_id: str
    brand: str
    model: str
    format_family: str
    relative_path: str
    expected_sha256: str
    actual_sha256: str | None
    present: bool
    regular_file: bool
    path_safe: bool
    hash_verified: bool
    bytes_unchanged: bool | None
    probe: dict[str, Any] | None
    passed: bool
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["errors"] = list(self.errors)
        return out


@dataclass(frozen=True)
class RawCorpusQualificationReport:
    manifest_sha256: str
    manifest_path: str
    samples_root: str
    corpus_id: str | None
    source_catalog: str | None
    policy: dict[str, Any]
    sample_count: int
    brand_count: int
    format_family_count: int
    decoder_identity_consistent: bool
    decoder_identity: dict[str, str] | None
    checks: dict[str, bool]
    samples: tuple[RawCorpusSampleResult, ...]
    errors: tuple[str, ...]
    passed: bool
    schema: str = RAW_CORPUS_QUALIFICATION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["samples"] = [s.to_dict() for s in self.samples]
        out["errors"] = list(self.errors)
        return out


def _is_sha256(text: str) -> bool:
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text.casefold())


def _safe_relative_path(text: str) -> PurePosixPath:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("relative_path must be non-empty")
    if "\\" in text:
        raise ValueError("relative_path must use portable '/' separators")
    p = PurePosixPath(text)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise ValueError(f"unsafe relative_path: {text!r}")
    if p.drive or p.root:
        raise ValueError(f"unsafe relative_path: {text!r}")
    return p


def _manifest_from_obj(obj: dict[str, Any]) -> RawCorpusManifest:
    if obj.get("schema") != RAW_CORPUS_MANIFEST_SCHEMA:
        raise ValueError(f"unexpected RAW corpus manifest schema {obj.get('schema')!r}")
    for field in ("corpus_id", "source_catalog", "source_base_url", "source_index_url", "catalog_snapshot_date", "rights_note"):
        if not isinstance(obj.get(field), str) or not obj[field].strip():
            raise ValueError(f"manifest field {field} must be non-empty text")
    if not str(obj["source_base_url"]).startswith("https://") or not str(obj["source_index_url"]).startswith("https://"):
        raise ValueError("RAW corpus public source URLs must use HTTPS")
    raw_samples = obj.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError("manifest must contain at least one sample")
    rows: list[RawCorpusSample] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for i, row in enumerate(raw_samples):
        if not isinstance(row, dict):
            raise ValueError(f"sample {i} must be an object")
        vals = {}
        for field in ("sample_id", "brand", "model", "format_family", "relative_path", "sha256"):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"sample {i} field {field} must be non-empty text")
            vals[field] = value.strip()
        sid = vals["sample_id"]
        if sid in ids:
            raise ValueError(f"duplicate sample_id {sid!r}")
        ids.add(sid)
        rel = _safe_relative_path(vals["relative_path"])
        rel_text = rel.as_posix()
        if rel_text.casefold() in paths:
            raise ValueError(f"duplicate relative_path {rel_text!r}")
        paths.add(rel_text.casefold())
        digest = vals["sha256"].casefold()
        if not _is_sha256(digest):
            raise ValueError(f"sample {sid!r} sha256 is invalid")
        suffix = Path(rel.name).suffix.casefold()
        if suffix not in RAW_SUFFIXES:
            raise ValueError(f"sample {sid!r} extension {suffix or '<none>'} is not in GPA RAW_SUFFIXES")
        family = vals["format_family"].casefold().lstrip(".")
        if family != suffix.lstrip("."):
            raise ValueError(
                f"sample {sid!r} format_family {vals['format_family']!r} does not match path extension {suffix!r}"
            )
        rows.append(RawCorpusSample(
            sample_id=sid,
            brand=vals["brand"],
            model=vals["model"],
            format_family=family.upper(),
            relative_path=rel_text,
            sha256=digest,
        ))
    return RawCorpusManifest(
        corpus_id=obj["corpus_id"].strip(),
        source_catalog=obj["source_catalog"].strip(),
        source_base_url=obj["source_base_url"].strip(),
        source_index_url=obj["source_index_url"].strip(),
        catalog_snapshot_date=obj["catalog_snapshot_date"].strip(),
        rights_note=obj["rights_note"].strip(),
        samples=tuple(rows),
    )


def load_raw_corpus_manifest(path: str | Path) -> tuple[RawCorpusManifest, str]:
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"invalid RAW corpus manifest JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("RAW corpus manifest top level must be an object")
    return _manifest_from_obj(obj), digest


def _candidate_path(root: Path, relative_path: str) -> tuple[Path | None, str | None]:
    """Resolve a manifest path without allowing symlink/path escape."""
    rel = _safe_relative_path(relative_path)
    try:
        root_resolved = root.resolve(strict=True)
    except Exception as e:
        return None, f"samples root is unavailable: {e}"
    if not root_resolved.is_dir():
        return None, "samples root is not a directory"

    cur = root_resolved
    for part in rel.parts:
        cur = cur / part
        try:
            if cur.is_symlink():
                return None, f"symlink component is not permitted: {cur}"
        except OSError as e:
            return None, f"could not inspect sample path component: {e}"
    try:
        resolved = cur.resolve(strict=False)
        resolved.relative_to(root_resolved)
    except Exception:
        return None, "sample path escapes samples root"
    return resolved, None


def _decoder_identity(probe: RawProbeResult) -> tuple[str, str, str] | None:
    if probe.status != "passed":
        return None
    if not probe.rawpy_version or not probe.libraw_version or not probe.validator_sha256:
        return None
    return (probe.rawpy_version, probe.libraw_version, probe.validator_sha256.casefold())


def qualify_raw_corpus(
    manifest_path: str | Path,
    samples_root: str | Path,
    *,
    timeout_seconds: float = 120.0,
    policy: RawCorpusPolicy | None = None,
) -> RawCorpusQualificationReport:
    """Qualify one exact local RAW corpus with the isolated decoder probe.

    Every local sample must match its manifest SHA-256 before probing and after probing.
    Every sample must decode successfully through one consistent rawpy/LibRaw build.
    The corpus must also satisfy the configured diversity floor.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    policy = policy or RawCorpusPolicy()
    manifest_path = Path(manifest_path).resolve()
    samples_root = Path(samples_root).resolve()

    top_errors: list[str] = []
    try:
        manifest, manifest_sha = load_raw_corpus_manifest(manifest_path)
    except Exception as e:
        return RawCorpusQualificationReport(
            manifest_sha256=sha256_file(manifest_path) if manifest_path.is_file() else "",
            manifest_path=str(manifest_path), samples_root=str(samples_root),
            corpus_id=None, source_catalog=None, policy=policy.to_dict(),
            sample_count=0, brand_count=0, format_family_count=0,
            decoder_identity_consistent=False, decoder_identity=None,
            checks={"manifest_valid": False, "diversity_sufficient": False, "all_samples_passed": False,
                    "one_exact_decoder_build": False, "all_bytes_unchanged": False},
            samples=(), errors=(f"manifest invalid: {e}",), passed=False,
        )

    sample_count = len(manifest.samples)
    brand_count = len({s.brand.casefold() for s in manifest.samples})
    family_count = len({s.format_family.casefold() for s in manifest.samples})
    diversity_ok = (
        sample_count >= policy.min_samples
        and brand_count >= policy.min_brands
        and family_count >= policy.min_format_families
    )
    if not diversity_ok:
        top_errors.append(
            f"corpus diversity below {policy.policy_id}: samples={sample_count}/{policy.min_samples}, "
            f"brands={brand_count}/{policy.min_brands}, format_families={family_count}/{policy.min_format_families}"
        )

    results: list[RawCorpusSampleResult] = []
    identities: list[tuple[str, str, str]] = []
    for sample in manifest.samples:
        errors: list[str] = []
        candidate, path_error = _candidate_path(samples_root, sample.relative_path)
        if path_error or candidate is None:
            errors.append(path_error or "unsafe sample path")
            results.append(RawCorpusSampleResult(
                sample.sample_id, sample.brand, sample.model, sample.format_family,
                sample.relative_path, sample.sha256, None, False, False, False, False,
                None, None, False, tuple(errors),
            ))
            continue
        present = candidate.exists()
        regular = candidate.is_file() if present else False
        if not present:
            errors.append("sample file is missing")
        elif not regular:
            errors.append("sample path is not a regular file")
        actual_before: str | None = None
        if regular:
            try:
                actual_before = sha256_file(candidate)
            except Exception as e:
                errors.append(f"could not hash sample before validation: {e}")
        hash_verified = actual_before == sample.sha256
        if actual_before is not None and not hash_verified:
            errors.append(f"sample SHA-256 mismatch: expected {sample.sha256}, got {actual_before}")

        probe: RawProbeResult | None = None
        unchanged: bool | None = None
        if regular and hash_verified:
            try:
                probe = probe_raw_isolated(candidate, timeout_seconds=timeout_seconds)
            except Exception as e:
                probe = RawProbeResult("failed", detail=f"RAW corpus probe raised unexpectedly: {e}")
            try:
                after = sha256_file(candidate)
                unchanged = after == actual_before == sample.sha256
                if not unchanged:
                    errors.append("sample bytes changed during supposedly read-only RAW corpus qualification")
            except Exception as e:
                unchanged = False
                errors.append(f"could not hash sample after validation: {e}")
            if probe.status != "passed":
                errors.append(f"RAW decode probe did not pass: {probe.status}: {probe.detail}")
            ident = _decoder_identity(probe)
            if ident is None and probe.status == "passed":
                errors.append("passing RAW probe omitted exact decoder identity")
            elif ident is not None:
                identities.append(ident)

        passed = bool(regular and hash_verified and probe is not None and probe.status == "passed" and unchanged and not errors)
        results.append(RawCorpusSampleResult(
            sample.sample_id, sample.brand, sample.model, sample.format_family,
            sample.relative_path, sample.sha256, actual_before, present, regular, True,
            hash_verified, unchanged, probe.to_dict() if probe else None, passed, tuple(errors),
        ))

    identity_set = set(identities)
    decoder_consistent = len(identity_set) == 1 and len(identities) == len(manifest.samples)
    if not decoder_consistent:
        top_errors.append("all samples must pass under one exact rawpy/LibRaw decoder build identity")
    decoder_obj = None
    if len(identity_set) == 1:
        rawpy_version, libraw_version, validator_sha = next(iter(identity_set))
        decoder_obj = {
            "rawpy_version": rawpy_version,
            "libraw_version": libraw_version,
            "validator_sha256": validator_sha,
        }

    all_samples = len(results) == len(manifest.samples) and all(r.passed for r in results)
    all_unchanged = len(results) == len(manifest.samples) and all(r.bytes_unchanged is True for r in results)
    checks = {
        "manifest_valid": True,
        "diversity_sufficient": diversity_ok,
        "all_samples_passed": all_samples,
        "one_exact_decoder_build": decoder_consistent,
        "all_bytes_unchanged": all_unchanged,
    }
    passed = all(checks.values()) and not top_errors
    return RawCorpusQualificationReport(
        manifest_sha256=manifest_sha,
        manifest_path=str(manifest_path),
        samples_root=str(samples_root),
        corpus_id=manifest.corpus_id,
        source_catalog=manifest.source_catalog,
        policy=policy.to_dict(),
        sample_count=sample_count,
        brand_count=brand_count,
        format_family_count=family_count,
        decoder_identity_consistent=decoder_consistent,
        decoder_identity=decoder_obj,
        checks=checks,
        samples=tuple(results),
        errors=tuple(top_errors),
        passed=passed,
    )


def write_raw_corpus_qualification_report(path: str | Path, report: RawCorpusQualificationReport) -> Path:
    path = Path(path)
    write_json_new(path, report.to_dict())
    return path


@dataclass(frozen=True)
class RawCorpusFetchSampleResult:
    sample_id: str
    relative_path: str
    source_url: str | None
    final_url: str | None
    destination: str
    expected_sha256: str
    actual_sha256: str | None
    bytes_downloaded: int
    status: str  # downloaded | reused | failed
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawCorpusFetchReport:
    manifest_path: str
    manifest_sha256: str
    samples_root: str
    corpus_id: str | None
    source_index_url: str | None
    source_index_final_url: str | None
    source_index_sha256: str | None
    source_index_verified: bool
    samples: tuple[RawCorpusFetchSampleResult, ...]
    downloaded: int
    reused: int
    failed: int
    passed: bool
    errors: tuple[str, ...] = ()
    schema: str = RAW_CORPUS_FETCH_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["samples"] = [s.to_dict() for s in self.samples]
        out["errors"] = list(self.errors)
        return out


def _parse_sha256_index(data: bytes) -> dict[str, str]:
    """Parse a coreutils-style SHA-256 index without normalizing source paths."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"source SHA-256 index is not UTF-8: {e}") from e
    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        if len(raw) < 67 or raw[64:66] not in ("  ", " *"):
            raise ValueError(f"malformed SHA-256 index line {lineno}")
        digest = raw[:64].casefold()
        rel = raw[66:]
        if not _is_sha256(digest) or not rel:
            raise ValueError(f"malformed SHA-256 index line {lineno}")
        _safe_relative_path(rel)
        if rel in out and out[rel] != digest:
            raise ValueError(f"conflicting hashes for source index path {rel!r}")
        out[rel] = digest
    if not out:
        raise ValueError("source SHA-256 index is empty")
    return out


def _open_https(url: str, timeout_seconds: float):
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"refusing non-HTTPS source URL: {url}")
    req = Request(url, headers={"User-Agent": "Google-Photos-Archive-Lab/RAW-corpus-qualification"})
    return urlopen(req, timeout=timeout_seconds)


def _response_final_url(response: Any, requested_url: str) -> str:
    try:
        final_url = str(response.geturl())
    except Exception:
        final_url = requested_url
    if urlparse(final_url).scheme.lower() != "https":
        raise ValueError(f"source redirected to non-HTTPS URL: {final_url}")
    return final_url


def _read_response_bytes(response: Any, *, max_bytes: int = 64 * 1024 * 1024) -> bytes:
    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray)):
            raise ValueError("source response returned non-byte data")
        chunk = bytes(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"source response exceeds {max_bytes} byte safety limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _ensure_fetch_parent(root: Path, rel: PurePosixPath) -> Path:
    """Create parent directories while refusing symlink components or root escape."""
    if root.exists() and root.is_symlink():
        raise ValueError("samples root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("samples root must not be a symlink")
    root_resolved = root.resolve(strict=True)
    if not root_resolved.is_dir():
        raise ValueError("samples root is not a directory")
    cur = root_resolved
    for part in rel.parts[:-1]:
        nxt = cur / part
        if nxt.exists() and nxt.is_symlink():
            raise ValueError(f"symlink component is not permitted: {nxt}")
        nxt.mkdir(exist_ok=True)
        if nxt.is_symlink():
            raise ValueError(f"symlink component is not permitted: {nxt}")
        resolved = nxt.resolve(strict=True)
        try:
            resolved.relative_to(root_resolved)
        except ValueError as e:
            raise ValueError("sample parent escapes samples root") from e
        cur = resolved
    return cur / rel.name


def fetch_raw_corpus(
    manifest_path: str | Path,
    samples_root: str | Path,
    *,
    timeout_seconds: float = 120.0,
    opener: Callable[[str, float], Any] | None = None,
) -> RawCorpusFetchReport:
    """Fetch an exact public RAW qualification corpus safely and without overwrite.

    The live source SHA-256 index must still bind every manifest path to the exact pinned
    digest before any sample is downloaded. Existing matching files are reused. Existing
    mismatches, symlinks, non-regular destinations, redirect-to-HTTP, transfer errors, or
    downloaded hash mismatches fail closed and are never overwritten.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    manifest_path = Path(manifest_path).resolve()
    raw_samples_root = Path(samples_root).absolute()
    if raw_samples_root.exists() and raw_samples_root.is_symlink():
        samples_root = raw_samples_root
    else:
        samples_root = raw_samples_root.resolve(strict=False)
    opener = opener or _open_https
    top_errors: list[str] = []
    try:
        manifest, manifest_sha = load_raw_corpus_manifest(manifest_path)
    except Exception as e:
        return RawCorpusFetchReport(
            str(manifest_path), "", str(samples_root), None, None, None, None, False,
            (), 0, 0, 1, False, (f"manifest invalid: {e}",),
        )

    index_sha: str | None = None
    final_index_url: str | None = None
    index_verified = False
    index_map: dict[str, str] = {}
    try:
        with opener(manifest.source_index_url, timeout_seconds) as response:
            final_index_url = _response_final_url(response, manifest.source_index_url)
            data = _read_response_bytes(response)
        index_sha = hashlib.sha256(data).hexdigest()
        index_map = _parse_sha256_index(data)
        mismatches = [s.relative_path for s in manifest.samples if index_map.get(s.relative_path) != s.sha256]
        if mismatches:
            raise ValueError(f"source SHA-256 index does not match {len(mismatches)} manifest sample(s)")
        index_verified = True
    except Exception as e:
        top_errors.append(f"source index verification failed: {e}")

    results: list[RawCorpusFetchSampleResult] = []
    if not index_verified:
        for sample in manifest.samples:
            results.append(RawCorpusFetchSampleResult(
                sample.sample_id, sample.relative_path, None, None,
                str(samples_root / Path(*PurePosixPath(sample.relative_path).parts)),
                sample.sha256, None, 0, "failed", "source index verification did not pass",
            ))
    else:
        base = manifest.source_base_url.rstrip("/") + "/"
        for sample in manifest.samples:
            rel = _safe_relative_path(sample.relative_path)
            source_url = urljoin(base, quote(rel.as_posix(), safe="/"))
            dest: Path | None = None
            temp: Path | None = None
            final_url: str | None = None
            actual: str | None = None
            downloaded = 0
            try:
                dest = _ensure_fetch_parent(samples_root, rel)
                if dest.exists() or dest.is_symlink():
                    if dest.is_symlink():
                        raise ValueError("destination is a symlink")
                    if not dest.is_file():
                        raise ValueError("destination exists but is not a regular file")
                    actual = sha256_file(dest)
                    if actual != sample.sha256:
                        raise ValueError(f"existing destination SHA-256 mismatch: {actual}")
                    results.append(RawCorpusFetchSampleResult(
                        sample.sample_id, sample.relative_path, source_url, None, str(dest),
                        sample.sha256, actual, 0, "reused", None,
                    ))
                    continue

                with opener(source_url, timeout_seconds) as response:
                    final_url = _response_final_url(response, source_url)
                    fd, rawtemp = tempfile.mkstemp(prefix=".gpa-raw-corpus-", dir=dest.parent)
                    temp = Path(rawtemp)
                    h = hashlib.sha256()
                    try:
                        with os.fdopen(fd, "wb") as out:
                            while True:
                                chunk = response.read(1024 * 1024)
                                if not chunk:
                                    break
                                if not isinstance(chunk, (bytes, bytearray)):
                                    raise ValueError("source response returned non-byte data")
                                chunk = bytes(chunk)
                                out.write(chunk); h.update(chunk); downloaded += len(chunk)
                            out.flush(); os.fsync(out.fileno())
                    except Exception:
                        try: os.close(fd)
                        except OSError: pass
                        raise
                actual = h.hexdigest()
                if actual != sample.sha256:
                    raise ValueError(f"downloaded SHA-256 mismatch: expected {sample.sha256}, got {actual}")
                commit_no_overwrite(temp, dest)
                temp = None
                committed = sha256_file(dest)
                if committed != sample.sha256:
                    raise ValueError("committed destination hash changed unexpectedly")
                results.append(RawCorpusFetchSampleResult(
                    sample.sample_id, sample.relative_path, source_url, final_url, str(dest),
                    sample.sha256, committed, downloaded, "downloaded", None,
                ))
            except Exception as e:
                if temp is not None:
                    temp.unlink(missing_ok=True)
                results.append(RawCorpusFetchSampleResult(
                    sample.sample_id, sample.relative_path, source_url, final_url,
                    str(dest if dest is not None else samples_root / Path(*rel.parts)),
                    sample.sha256, actual, downloaded, "failed", str(e),
                ))

    downloaded_count = sum(r.status == "downloaded" for r in results)
    reused_count = sum(r.status == "reused" for r in results)
    failed_count = sum(r.status == "failed" for r in results)
    passed = index_verified and failed_count == 0 and len(results) == len(manifest.samples)
    return RawCorpusFetchReport(
        manifest_path=str(manifest_path), manifest_sha256=manifest_sha, samples_root=str(samples_root),
        corpus_id=manifest.corpus_id, source_index_url=manifest.source_index_url,
        source_index_final_url=final_index_url, source_index_sha256=index_sha, source_index_verified=index_verified,
        samples=tuple(results), downloaded=downloaded_count, reused=reused_count,
        failed=failed_count, passed=passed, errors=tuple(top_errors),
    )


def write_raw_corpus_fetch_report(path: str | Path, report: RawCorpusFetchReport) -> Path:
    path = Path(path)
    write_json_new(path, report.to_dict())
    return path
