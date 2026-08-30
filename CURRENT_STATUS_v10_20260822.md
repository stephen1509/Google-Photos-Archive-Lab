# Google Photos Archive Lab — Status v10

Updated: 2026-08-22 JST

## Reproducible milestone
- **333 passed, 0 failed**.
- One intentional adversarial duplicate-ZIP-name warning from the test corpus.
- Python compile pass succeeds.
- Checkpoint ZIP integrity verified.
- Full suite rerun successfully from a fresh extraction outside the development tree.

## New in v10
- Added isolated ExifTool candidate qualification schema `gpa.exiftool-qualification.v1`.
- Harness ID: `gpa.exiftool-jpeg-roundtrip.v1`.
- Exact candidate provenance includes ExifTool version, executable SHA-256, optional distribution-tree SHA-256, and stable qualification ID.
- Candidate executable and distribution symlinks are rejected.
- Disposable JPEG qualification writes capture date/time, UTC offset, GPS, XMP capture date and description, then reads them back.
- Qualification requires the whole file to change while the JPEG visual/media payload remains byte-identical and the output remains decodable.
- No-op/fake-success writers fail qualification; media-changing/recompressing writers fail qualification; incorrect readback fails qualification.
- Qualification reports are machine-readable and write-once.
- CLI adds `gpa-lab qualify-exiftool --executable ... --workdir ... [--distribution-root ...] [--report ...]`.
- Candidate qualification and production promotion are deliberately separate.
- Exact-build production allow-list remains EMPTY; production embedded-media writes therefore still fail closed.

## Major blockers
1. Run the harness on genuine upstream ExifTool 13.55 production and 13.59 development distributions.
2. Expand exact-build qualification to HEIC/HEIF, AVIF, MOV/MP4, Android Motion Photos, Apple Live Photos and representative RAW.
3. Qualified RAW validation matrix.
4. Broader genuine Live/Motion Photo corpus.
5. Production offline GeoNames/timezone data packs.
6. Large real-media/video and crash/fault runs.
7. Representative real Google Photos Takeout samples.
8. Windows-specific release qualification and GUI/installer.

## Safety position
Do **not** delete Google originals based on this development build. No exact ExifTool build is promoted for production embedded-media writing.
