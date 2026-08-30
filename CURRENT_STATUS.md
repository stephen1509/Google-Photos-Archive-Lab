# Google Photos Archive Lab — Current Status

Updated: 2026-08-22 JST

## Verified milestone
- **312 passed, 1 conditional HEIC-encoder test skipped, 0 failed** in the current working tree.
- Baseline restored from clean-room-tested v5 (300/300) before this tranche.
- `python -m py_compile` and full pytest suite pass in the working tree.

## New in this tranche
- Frozen `gpa.archive.v1` archive-format manifest and explicit metadata-only upgrade path.
- Future/unknown archive formats fail closed before execution.
- Plan/storage-preflight remain read-only and do not create archive-format state.
- Per-asset media validation is preserved in immutable GPA revisions with validator name/version provenance.
- Pillow validation for supported stills; libheif decode validation for HEIC/HEIF/AVIF when available; ffprobe generic video validation requires a real video track; RAW is explicitly unavailable until qualified.
- Failed/corrupt media is still preserved byte-for-byte; validation failure is recorded and blocks the eventual Google-retirement gate rather than discarding source data.
- Auditor verifies validator provenance for every current asset claiming validation `passed`.
- Verification report exposes passed/failed/unavailable counts and requires complete qualified media validation before recommending Google retirement.
- Real AVIF was exercised through ZIP → planning → output → immutable record → independent audit while preserving bytes exactly.

## Major blockers remaining
1. Real ExifTool qualification, especially HEIC/HEIF/AVIF/RAW/MOV embedded metadata write/read round trips with payload-integrity verification.
2. Qualified RAW media decoding/validation matrix.
3. Genuine Apple Live Photo / Android Motion Photo corpus expansion.
4. Production packaging of signed/versioned offline GeoNames and historical timezone data packs.
5. Large real-media/video stress runs and additional crash/fault injection.
6. Real representative Google Photos Takeout samples from the user before release qualification.
7. Windows GUI/installer after the reconstruction engine is sufficiently frozen.

## Safety position
Do **not** delete Google Photos/Google data based on this development build. The project is deliberately designed to refuse the final retirement recommendation until all preservation, completeness, validation, and redundant-storage evidence is satisfied.
