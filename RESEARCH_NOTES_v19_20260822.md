# Google Photos Archive Lab — Research Notes v19 Delta

Updated: 2026-08-22 JST

## v19 — Parallel-authority reconciliation and bounded complete-suite hardening
Two independently preserved v16 descendants existed at the same time. One branch advanced backup/source-vault link isolation, per-record Source Vault binding, orphan-byte detection, and later physical-storage independence evidence. A separately reconciled v16 branch added bounded ExifTool subprocesses, more comprehensive Source Vault + finished-archive mirror fault qualification, `MirrorBuildReport`/`materialize_verified_mirror`, and a machine-readable bounded complete-suite release gate. Neither branch alone contained every improvement.

v19 was built by a true three-way merge using the earlier 348-test v16 checkpoint as the common ancestor. The reconciled 349-test v16 checkpoint was independently recovered at SHA-256 `8d974bf4ca03dc94646cf5868bd0d5e4cf2ddc6c37049ae02cfd1ae8f07e05d1`; the later branch carried the v17/v18 safety hardening. The merge deliberately retains both mirror APIs for compatibility: `materialize_verified_mirror()` provides detailed copied/reused/verified counts, while `synchronize_mirror()` returns the final verification report. Both use the same full-preflight, staged, fsynced, no-overwrite, link/junction-rejecting implementation.

The Source Vault fault hook is standardized at the precise durability boundary: it fires only after a new content-addressed byte object has been committed and verified, before the occurrence record is written. A rerun that sees the already-existing exact bytes does not fire the hook again and can therefore heal the missing provenance record. Source Vault audit still rejects link/junction traversal, binds every occurrence record to exact `(size, SHA-256)` evidence, and inventories orphan byte objects.

The bounded release runner now includes `tests/test_backup_recovery.py`, disables third-party pytest plugin autoload inside each isolated subprocess, and supports bounded parallel module execution. Each module still runs in a separate Python process with its own timeout and JUnit XML. The v19 release evidence records **363 tests, 0 failures, 0 errors, 0 skipped**, across eight isolated modules.

The merged `gpa.stress-backup-recovery.v1` qualification used 20 raw Takeout ZIPs, a 40-file Source Vault mirror, and a 100-file finished-archive mirror. Deliberate Source Vault byte/provenance interruption, Source Vault mirror interruption, and finished-archive mirror interruption all healed on rerun; original Takeout hashes were unchanged, mirrored Source Vault audit passed, mirrors verified, and no GPA staging files remained.

A later persistent-library check found that a separate v18 reconciliation checkpoint had already been promoted while the merge was being prepared. That checkpoint was recovered independently at SHA-256 `6e0104dc0655e7a91128fe7f4f30008950595fbf5d6e26777f1d252578a83329` and reproduced at **355/355**. Direct source comparison showed that its ExifTool execution/qualification implementation is already present unchanged in v19. The remaining differences are intentional extensions in v19: detailed mirror materialization/reporting, broader Source Vault + finished-archive fault qualification, storage-identity/retirement evidence v2, and bounded parallel release-gate execution.

## Current external research position
- ExifTool 13.55 remains the production candidate identified by prior research; 13.59 is a later development candidate. Exact candidate bytes are still required before qualification.
- Production ExifTool exact-build allow-list remains empty; embedded writes remain disabled.
- Different filesystem/volume identifiers do **not** prove different physical disks. Windows release qualification must interrogate the underlying disk extents/device topology, and NAS/SMB paths require a separate fail-closed evidence lane.

## Forward rule
Future work must use v19 or a later independently clean-reproduced checkpoint as the base. Do not branch forward from v16/v17/v18.
