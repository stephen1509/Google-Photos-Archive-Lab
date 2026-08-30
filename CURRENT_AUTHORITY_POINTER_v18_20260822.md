# Google Photos Archive Lab — Current Authority Pointer v18

Updated: 2026-08-22 JST

## Authoritative reproducible checkpoint
- Version: v18
- Binary: `google_photos_archive_lab_checkpoint_20260822_v18.zip`
- Persistent Library path: `/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v18.zip`
- SHA-256: `6e0104dc0655e7a91128fe7f4f30008950595fbf5d6e26777f1d252578a83329`
- Final clean-extraction suite: **355 passed, 0 failed**.
- One intentional adversarial duplicate-ZIP-name warning is expected.
- Compile pass succeeds for `src`, `tests`, and `tools`.
- ZIP integrity verified.

## Reconciliation
v18 is based on independently reproduced v17 and retains all v17 Source Vault/mirror link/provenance hardening while merging the later bounded ExifTool subprocess and hung-candidate regression work. It supersedes both v17 and the reconciled v16 branch.

## Safety
Production ExifTool embedded-media allow-list remains empty. Embedded writes remain disabled. Google retirement remains blocked until all source-completeness, validation, audit, physical-backup and exit-evidence gates pass.
