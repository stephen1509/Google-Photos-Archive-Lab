# Google Photos Archive Lab — Status v19

Updated: 2026-08-22 JST

## Reproducible milestone
- **363 passed, 0 failed, 0 skipped** in the merged source tree and **363/363 again from the final fresh checkpoint extraction**.
- Eight isolated pytest modules are included in machine-readable `gpa.test-suite.v1` release evidence.
- One intentional adversarial duplicate-ZIP-name warning remains in the normal raw pytest presentation.
- Package version: **0.0.6**.

## Authority reconciliation
- Common ancestor: earlier v16 checkpoint, 348 tests.
- Reconciled parallel v16 authority: 349 tests, checkpoint SHA-256 `8d974bf4ca03dc94646cf5868bd0d5e4cf2ddc6c37049ae02cfd1ae8f07e05d1`.
- Persisted reconciliation authority v18: independently restored and reproduced at **355/355**, checkpoint SHA-256 `6e0104dc0655e7a91128fe7f4f30008950595fbf5d6e26777f1d252578a83329`; it combines v17 backup/Source-Vault hardening with bounded ExifTool execution.
- v19 was compared directly with the persisted v18 source. ExifTool executable/qualification code is identical; v19 retains its bounded-tool behavior while adding the remaining mirror, Source Vault, storage-evidence and release-gate improvements.
- v19 is the reconciled descendant of all preserved lines; no v16/v17/v18 checkpoint should be used as a forward base after v19 promotion.

## Retained reconciled-v16 improvements
- ExifTool version/read/write subprocesses have explicit fail-closed timeouts.
- ExifTool candidate qualification has its own bounded subprocess timeout.
- `materialize_verified_mirror()` returns detailed copied/reused/verified counts and uses whole-copy preflight, staged fsynced commits, no overwrite, post-commit hashing, and resumable exact-match reuse.
- Comprehensive Source Vault + Source Vault mirror + finished-archive mirror fault qualification.
- Machine-readable bounded complete-suite release runner `gpa.test-suite.v1`.

## Retained v17/v18 improvements
- Symlink and Windows junction/reparse-style link isolation in mirror/source-vault paths.
- Mirror verification exposes unsafe paths and refuses link-backed expected objects even when target bytes hash correctly.
- Every Source Vault occurrence record must agree with exact verified `(size, SHA-256)` byte-object binding.
- Orphan Source Vault byte objects after interrupted provenance commits are audited visibly.
- `gpa.storage-identity.v2` records durable storage-independence evidence.
- `gpa.verification.v2` embeds separate storage evidence for finished-archive and raw Source Vault redundancy.
- Same-path/same-volume machine evidence cannot be overridden by an operator confirmation flag.
- Different volume identifiers are not treated as proof of separate physical disks.

## v19 release-gate hardening
- Bounded suite runner includes `tests/test_backup_recovery.py`.
- Each pytest subprocess disables third-party plugin autoload.
- Runner supports bounded parallel module execution while preserving one Python process and one timeout per test module.
- Pre-check report: **363 tests / 0 failures / 0 errors / 0 skipped**, 8 modules, no module timeout.

## v19 backup fault qualification
- 20 raw Takeout ZIPs: original SHA-256 unchanged.
- Source Vault: 20 records / 20 archives / 0 audit problems.
- Source Vault mirror: 40 files; deliberate interruption then 3 reused + 37 copied on healing; final verify PASS; mirrored vault audit PASS.
- Finished archive mirror: 100 files; deliberate interruption then 5 reused + 95 copied on healing; final verify PASS.
- No GPA staging files remained.

## Current external-tool position
- Genuine upstream ExifTool 13.55/13.59 exact candidate bytes are still required before qualification.
- Production ExifTool exact-build allow-list remains EMPTY.
- Embedded media rewriting remains disabled.

## Major blockers remaining
1. Genuine ExifTool 13.55/13.59 exact-build qualification.
2. Format write matrix: HEIC/HEIF, AVIF, MOV/MP4, Motion/Live Photos, RAW.
3. Qualified RAW decode/media-validation matrix.
4. Broader genuine Apple Live Photo / Android Motion Photo corpus.
5. Production offline GeoNames + historical timezone packs.
6. Real separate-device storage qualification, especially Windows/NTFS volume/device identity, junctions, removable storage and NAS.
7. Larger long/large-video and hardware/fault qualification.
8. Representative real Google Photos Takeout samples.
9. Windows-specific release qualification and final GUI/installer.

## Final checkpoint
- Binary: `google_photos_archive_lab_checkpoint_20260822_v19.zip`
- Persistent Library path: `/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v19.zip`
- Size: **181962 bytes**
- SHA-256: `0bb74224ae2c3f17a4368c5896a9e24fa3b904372262191cb4167dbb631ee941`
- ZIP integrity: PASS
- Fresh-extraction compile: PASS
- Fresh-extraction bounded suite: **363 tests / 0 failures / 0 errors / 0 skipped**

## Safety position
Do **not** delete Google originals based on this development build. Google retirement remains blocked until every completeness, audit, qualified validation, Source Vault, independent physical redundancy and exit-evidence gate passes.
