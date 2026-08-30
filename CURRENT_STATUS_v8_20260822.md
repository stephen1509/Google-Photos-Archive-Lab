# Google Photos Archive Lab — Status v8

Updated: 2026-08-22 JST

## Reproducible milestone
- **320 passed, 1 conditional HEIC-encoder integration test skipped, 0 failed**.
- Python compile pass succeeds.
- Checkpoint ZIP integrity verified and full suite rerun from a fresh extraction outside the project tree.

## New in v8
- Added isolated ExifTool candidate qualification schema `gpa.exiftool-qualification.v1`.
- Candidate qualification requires: metadata write actually changes whole-file bytes; JPEG entropy-coded visual payload stays byte-identical; output remains decodable; capture date, GPS and description read back correctly.
- A fake ExifTool that only prints a success message without changing metadata fails qualification.
- Added `gpa-lab qualify-exiftool --executable ... --workdir ... --report ...` for reproducible machine-readable qualification runs.
- Passing a candidate test does not automatically approve it for production writes; release promotion remains a separate explicit step.

## Retained safety controls
- Exact validator build fingerprints and qualification IDs.
- Auditor rejects unqualified validator builds.
- ExifTool writes fail closed unless an exact `(version, executable SHA-256)` pair is explicitly approved.
- Default ExifTool production write allow-list remains empty.

## Major blockers
1. Run the qualification harness on genuine upstream ExifTool 13.55 and 13.59 distributions when they can be transferred into the sandbox; expand to PNG/HEIC/AVIF/MOV/Motion Photo.
2. Qualified RAW matrix.
3. Broader genuine Live/Motion Photo corpus.
4. Production offline GeoNames/timezone packs.
5. Large real-media/video and fault-injection runs.
6. Representative real Google Photos Takeout samples.
7. Windows-specific release qualification + GUI/installer.

## Safety position
Do not delete Google originals based on this development build. Embedded metadata rewriting remains disabled until a genuine exact ExifTool build completes qualification.
