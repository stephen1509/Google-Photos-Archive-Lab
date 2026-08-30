# Google Photos Archive Lab — Status v16

Updated: 2026-08-22 JST

## Reproducible milestone
- **348 passed, 0 failed**.
- One intentional adversarial duplicate-ZIP-name warning from the test corpus.
- Python compile pass succeeds.
- Checkpoint ZIP integrity verified.
- Full suite rerun successfully from a fresh extraction outside the development tree: **348 passed, 0 failed**.
- Preserved v15 was independently restored first and reproduced at 343/343, establishing the starting baseline from actual saved source rather than chat memory.

## New in v16
- Added Source Vault crash/restart qualification after immutable raw Takeout ZIP bytes commit but before provenance occurrence record creation.
- Restart verifies and reuses exact SHA-256-matching raw ZIP bytes and creates the missing occurrence record without duplicating source archives.
- Added resumable `synchronize_mirror()` for redundant archive copies using staged, fsynced, no-overwrite commits.
- Mirror synchronization validates the tree-manifest schema and paths, verifies every source object, and preflights every existing destination object before any new copy is committed.
- Conflicting destination bytes fail closed before further backup mutation; extras are never silently deleted.
- Added unsafe/path-traversal and duplicate manifest-path rejection.
- Added `gpa.backup-fault-recovery.v1` stress qualification and five automated regression tests.

## 100-file backup fault qualification
- 101 manifest objects total (100 payload files + one metadata note).
- Deliberate mirror interruption after 4 commits correctly reported 97 missing.
- Healing rerun converged to 101/101 verified files.
- Deliberate corrupt destination blocked synchronization before another missing file could be written.
- Source Vault power-loss simulation left durable raw ZIP bytes but incomplete audit state; normal rerun reused those bytes and converged to 1 archive / 1 provenance record / 0 audit problems.

## Checkpoint
- File: `google_photos_archive_lab_checkpoint_20260822_v16.zip`
- Size: 165292 bytes
- SHA-256: `6493423d498010f3556b4f97e15c63ce740ce0e2fc784cd350e9e28d4b9a1fa4`
- Persistent binary location: ChatGPT Library `/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v16.zip`

## Major blockers remaining
1. Genuine ExifTool 13.55/13.59 exact-build qualification.
2. Format write matrix: HEIC/HEIF, AVIF, MOV/MP4, Motion/Live Photos, RAW.
3. Qualified RAW decode/media-validation matrix.
4. Broader genuine Apple Live Photo / Android Motion Photo corpus.
5. Production offline GeoNames + historical timezone packs.
6. Larger mixed real-media/video stress, including long/large video files and real separate storage devices.
7. Representative real Google Photos Takeout samples.
8. Windows-specific release qualification.
9. Final GUI/installer.

## Safety position
Do **not** delete Google originals based on this development build. Production embedded-media writing remains disabled, and Google retirement still requires all completeness, audit, validation, source-vault, independent-physical-backup, and exit-evidence gates.
