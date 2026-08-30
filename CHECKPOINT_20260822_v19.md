# Google Photos Archive Lab — Checkpoint v19

Updated: 2026-08-22 JST

## Authority basis
- Persisted v18 authority independently restored and reproduced: **355/355**; SHA-256 `6e0104dc0655e7a91128fe7f4f30008950595fbf5d6e26777f1d252578a83329`.
- Reconciled v16/349 authority retained bounded ExifTool execution, detailed verified mirror materialization, comprehensive backup fault qualification and bounded release testing.
- v17 backup-link/Source-Vault hardening retained.
- Physical-storage independence evidence hardening retained.
- Direct source comparison confirmed v19 contains the persisted v18 bounded ExifTool implementation unchanged while extending the remaining safety lanes.

## Verified state
- Package version: **0.0.6**.
- Source-tree bounded complete suite: **363 tests, 0 failures, 0 errors, 0 skipped**.
- Comprehensive `gpa.stress-backup-recovery.v1`: PASS.
- Python compile: PASS.
- ZIP integrity: PASS.
- Fresh extraction outside development tree: PASS.
- Fresh-extraction compile: PASS.
- Fresh-extraction bounded complete suite: **363 tests, 0 failures, 0 errors, 0 skipped**.
- All 8 isolated test modules passed; no module timed out.

## Binary checkpoint
- Persistent Library path: `/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v19.zip`
- Size: **181962 bytes**
- SHA-256: `0bb74224ae2c3f17a4368c5896a9e24fa3b904372262191cb4167dbb631ee941`

## Machine-readable evidence
- `GPA_COMPLETE_SUITE_v19_CLEAN_FINAL.json` — `gpa.test-suite.v1` clean-extraction report.
- `GPA_STRESS_BACKUP_FAULT_v19.json` — `gpa.stress-backup-recovery.v1` fault/recovery report.

## Authority
v19 supersedes the persisted v18/355 checkpoint and the earlier reconciled v16/v17 lines. Future work must start from v19 or a later independently clean-reproduced checkpoint. Production ExifTool embedded-media writing remains disabled.
