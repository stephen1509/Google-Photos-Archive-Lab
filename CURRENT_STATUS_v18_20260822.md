# Google Photos Archive Lab — Status v18

Updated: 2026-08-22 JST

## Reproducible milestone
- **355 passed, 0 failed** across the complete automated suite.
- One intentional adversarial duplicate-ZIP-name warning remains expected.
- Preserved v17 was independently restored first: checkpoint SHA-256 matched, compile passed, and **354/354** tests reproduced from clean extraction.
- Final v18 checkpoint ZIP integrity passed; a fresh extraction compiled cleanly and reproduced **355/355** tests.

## New in v18 — authority reconciliation + bounded external tools
- Reconciled two independently preserved development lines without discarding either: v17 Source Vault/mirror symlink-junction and provenance hardening, plus the later v16 ExifTool timeout and bounded-suite work.
- `ExifToolAdapter` now applies a configurable positive timeout to version/read/write subprocess calls and fails closed on timeout.
- ExifTool candidate qualification applies a separate bounded subprocess timeout.
- Added an adversarial hung-candidate regression.
- Added `tools/run_complete_suite.py` (`gpa.test-suite.v1`) for fresh-process, per-module bounded release testing, including v17's backup-recovery module.
- Package version: 0.0.5.

## Retained v17 hardening
- Backup manifests and verification reject symlink/junction-backed source or destination objects.
- Mirror synchronization preflights unsafe links and destination conflicts before missing files are committed.
- Source Vault audit binds occurrence records to verified size/SHA-256 byte objects, detects orphan byte objects, rejects conflicting record bindings, and fails closed on link-like vault paths.

## Qualification evidence
`gpa.backup-fault-recovery.v1`: PASS.
- 101 expected mirror objects.
- Deliberate interruption after 4 commits; 97 missing detected.
- Healing rerun: 101/101 verified.
- Destination conflict blocked before additional mutation.
- Source Vault post-byte-commit interruption recovered by exact-byte reuse and clean audit.

## Checkpoint
- File: `google_photos_archive_lab_checkpoint_20260822_v18.zip`
- Persistent Library path: `/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v18.zip`
- SHA-256: `6e0104dc0655e7a91128fe7f4f30008950595fbf5d6e26777f1d252578a83329`

## External-tool position
- Production ExifTool embedded-media allow-list remains EMPTY.
- Genuine upstream ExifTool candidate bytes are still required before any exact-build qualification can be promoted.
- Embedded media writing remains disabled.

## Major blockers remaining
1. Genuine upstream ExifTool exact-build qualification and version-selection review.
2. Format write matrix: HEIC/HEIF, AVIF, MOV/MP4, Motion/Live Photos, RAW.
3. Qualified RAW decode/media-validation matrix.
4. Broader genuine Apple Live Photo / Android Motion Photo corpus.
5. Production offline GeoNames + historical timezone packs.
6. Larger mixed real-media/video stress, including long/large video files and real storage devices.
7. Representative real Google Photos Takeout samples.
8. Windows-specific filesystem/device/release qualification, including actual NTFS junction/reparse-point tests.
9. Final GUI/installer.

## Safety position
Do **not** delete Google originals based on this development build. Google retirement remains gated on source completeness, qualified media/tool coverage, archive/source-vault audits, independently verified copies on separate physical media, and exit evidence.
