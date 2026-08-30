# Google Photos Archive Lab — Status v7

Updated: 2026-08-22 JST

## Reproducible milestone
- **317 passed, 1 conditional HEIC-encoder integration test skipped, 0 failed**.
- Python compile pass succeeds.
- Checkpoint ZIP integrity verified and full suite rerun from a fresh extraction outside the project tree.

## New in v7
- ffprobe/libheif validator records now include SHA-256 of the exact executable used; Pillow records the fingerprint of its native imaging component.
- Qualified-validator registry binds qualification IDs to exact validator version + binary/component fingerprint.
- Independent audit rejects passed validation if validator version/fingerprint is missing or the qualification ID is not valid for that exact build.
- ExifTool adapter now distinguishes reviewed read versions (13.55 and 13.59) from write qualification.
- ExifTool writing is **fail-closed by default**. Writes require an explicitly approved exact `(version, executable SHA-256)` pair. The default write allow-list is empty because genuine upstream ExifTool distributions have not yet completed round-trip qualification in this sandbox.
- Same version number with different ExifTool bytes is explicitly rejected for writing.

## Current blockers
1. Real upstream ExifTool 13.55/13.59 read-write-read qualification with JPEG/PNG/HEIC/AVIF/MOV and media-payload integrity verification.
2. Qualified RAW validation/read matrix.
3. Broader genuine Live Photo / Motion Photo corpus.
4. Production offline place/timezone data packs.
5. Very-large real-media/video and additional crash/fault tests.
6. Real representative Google Photos Takeout samples.
7. Windows-specific validator qualification + GUI/installer.

## Safety position
Do not delete the Google originals based on this development build. ExifTool in-file writing remains disabled until an exact build passes independent qualification.
