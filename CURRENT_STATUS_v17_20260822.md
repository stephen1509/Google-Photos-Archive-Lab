# Google Photos Archive Lab — Status v17

Updated: 2026-08-22 JST

## Reproducible milestone
- **354 passed, 0 failed**.
- One intentional adversarial duplicate-ZIP-name warning from the test corpus.
- Persisted v16 was independently restored first and reproduced at 348/348.
- Python compile pass succeeds.
- Checkpoint ZIP integrity verified.
- Full suite rerun successfully from a fresh extraction outside the development tree: **354 passed, 0 failed**.

## New in v17 — backup-link and Source Vault provenance hardening
- Redundant-copy manifests reject symlink/junction-backed source objects.
- Mirror verification refuses expected destinations through symlinks/junctions even if target bytes hash correctly.
- Mirror synchronization preflights link-like source/destination components before any missing backup file is committed.
- Mirror reports include `unsafe_paths` and cannot report `ok` while unsafe links are present.
- Source Vault audit binds every occurrence record to an exact verified (size, SHA-256) byte object.
- Conflicting duplicate occurrence metadata cannot inject an unverified digest into the verified source set.
- Orphan vaulted byte objects left after a byte-commit / pre-record crash are detected even when older valid records already exist.
- Link-like Source Vault record/archive paths fail closed.
- Six new automated adversarial regressions; package version 0.0.4.

## Retained stress qualification
- `gpa.backup-fault-recovery.v1`: PASS.
- 101 expected mirror objects.
- Deliberate interruption after 4 commits: 97 missing correctly detected.
- Healing rerun: 101/101 verified.
- Destination conflict blocked before additional backup mutation.
- Source Vault interrupted byte commit recovered by exact-byte reuse and clean audit.

## Checkpoint
- File: `google_photos_archive_lab_checkpoint_20260822_v17.zip`
- Persistent Library path: `/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v17.zip`
- Size: 168376 bytes
- SHA-256: `f766145e38699910e6f7c87553c4a356897fc12ad8be360838649f8dad50a3f5`

## Current external-tool position
- Upstream ExifTool history identifies 13.55 as the production release and 13.59 as a later development release.
- Genuine exact candidate bytes are still required before the qualification harness can produce evidence here.
- Production ExifTool embedded-media allow-list remains EMPTY.

## Major blockers remaining
1. Genuine ExifTool 13.55/13.59 exact-build qualification.
2. Format write matrix: HEIC/HEIF, AVIF, MOV/MP4, Motion/Live Photos, RAW.
3. Qualified RAW decode/media-validation matrix.
4. Broader genuine Apple Live Photo / Android Motion Photo corpus.
5. Production offline GeoNames + historical timezone packs.
6. Larger mixed real-media/video stress, including long/large video files and real storage devices.
7. Representative real Google Photos Takeout samples.
8. Windows-specific release qualification, including actual NTFS junction/reparse-point tests.
9. Final GUI/installer.

## Safety position
Do **not** delete Google originals based on this development build. Embedded media writing remains disabled, and Google retirement still requires all source-completeness, audit, media-validation, independent-physical-backup and exit-evidence gates.
