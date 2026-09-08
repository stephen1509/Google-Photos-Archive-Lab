# Windows qualification evidence reconciliation — 2026-09-08

This document supersedes the unsupported Windows full-regression success claim
at the branch tip.  It does not alter or delete historical evidence.

## Provenance and contradiction

- `STATUS.md` and `qualification/GPA_FULL_REGRESSION_v52_windows_r1.json` were
  introduced by `d05e37017f0c3acc3b928dd65ac8bea282d1c9e7`.  They claim 671
  passed tests under `C:\Python314\python.exe` but provide no JUnit artifact,
  source-tree hash, dependency lock, or runner report that reproduces that
  result.
- `qualification/GPA_COMPLETE_SUITE_v52_windows.json` and
  `qualification/GPA_PYTEST_v52_windows.xml` were introduced earlier by
  `9c16042ac5e00d87121fa3b6228232ccb4801a14`.  They instead record 107
  failures and 50 failures respectively.
- `TAKEOUT_QUALIFICATION_HANDOFF.md` repeated the Python 3.14/FFmpeg readiness
  assertion but did not provide a full-suite artifact.  It is not full-suite
  evidence.

Therefore **671 passed is historical, uncorroborated prose, not a current
qualification result**.  It must not be used as a restoration-authority or
Takeout-readiness gate.  Preserve the files above for audit history; only a
new machine-readable complete-suite report from this repair branch may
supersede them.

## Reproducible Windows prerequisites

- Python is supported by project metadata at `>=3.11`; Python 3.14.4 was an
  evidence host, not a declared source requirement.
- QuickTime, Motion Photo, and Live Photo synthetic tests require both
  `ffmpeg` and `ffprobe` on the process PATH.
- Positive AVIF qualification additionally requires `GPA_HEIF_CONVERT` to name
  an AV1-capable `heif-convert`.  The prior evidence used libheif 1.23.1,
  executable SHA-256 `f9723cf7c2bb635a2e1077ac177108e78d6697262ee93e01acfbf25e59b4db96`,
  with the built-in AOM AV1 decoder and `aom` DLL SHA-256
  `77bb8f8798768a9ed212db24bc61eb2421c83fbe28e0f6045424d5a52d833edc`.
  FFmpeg 8.0 was recorded for video fixtures, but FFmpeg alone does not prove
  the AVIF decode capability.
- Tests needing a real symbolic link skip only when Windows denies symlink
  creation.  That leaves the security behavior unqualified on that host rather
  than disguising it as a pass.

## Current repair-gate rule

A future authority checkpoint requires two fresh complete-suite reports with
zero failures, zero errors, zero timeouts, matching intentional skip reasons,
and a recorded toolchain identity.  Until then the 20260830 v52 Dropbox ZIP
remains the only restoration authority.
