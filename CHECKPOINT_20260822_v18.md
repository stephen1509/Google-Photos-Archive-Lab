# Google Photos Archive Lab — Checkpoint v18

Date: 2026-08-22 JST

## Basis
- Persisted v17 binary checkpoint restored independently.
- Saved SHA-256 matched exactly.
- ZIP integrity clean.
- Compile clean.
- Full clean-extraction v17 suite reproduced: **354 passed, 0 failed**.

## v18 delta
- Preserved all v17 backup-link and Source Vault provenance hardening.
- Merged bounded ExifTool version/read/write subprocess handling from the later v16 branch.
- Merged bounded ExifTool candidate qualification timeout handling.
- Added hung-candidate fail-closed regression.
- Added/updated isolated complete-suite runner to include all eight current test modules.
- Package version 0.0.5.

## Final verification
- Complete automated suite: **355 passed, 0 failed**.
- One intentional duplicate-ZIP-name warning.
- `python -m compileall -q src tests tools` passes.
- Final checkpoint ZIP integrity verified.
- Fresh extraction compiled cleanly and reproduced **355/355** tests.

## Checkpoint binary
- Persistent Library path: `/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v18.zip`
- SHA-256: `6e0104dc0655e7a91128fe7f4f30008950595fbf5d6e26777f1d252578a83329`

## Backup fault qualification
- schema: `gpa.backup-fault-recovery.v1`
- expected mirror files: 101
- deliberate interruption after 4 commits
- partial missing detected: 97
- healed mirror: 101/101 verified
- source-vault interruption recovered by exact-byte reuse
- final source-vault audit: clean
- destination conflict preflight: blocked before extra mutation

## Safety
- ExifTool production allow-list remains empty.
- Embedded writes remain disabled.
- Separate-physical-media confirmation remains mandatory for Google retirement.
