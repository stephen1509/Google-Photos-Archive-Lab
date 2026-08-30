# Research Notes v7 — 2026-08-22

## Validator provenance
- A validator version string alone is insufficient archival provenance. Command-based validators now record SHA-256 of the exact executable used; Pillow records its native imaging-component fingerprint.
- Qualified media validation is tied to an explicit qualification ID for the exact validator version + fingerprint exercised by the regression corpus.
- The current Linux lab registry covers Pillow 12.3.0, ffprobe 7.1.5-0+deb13u1 and libheif/heif-convert 1.19.8 with exact local fingerprints. Final Windows builds must be independently qualified and must not inherit these Linux IDs.

## ExifTool policy correction
- ExifTool upstream currently identifies 13.55 as the latest production release and 13.59 as a later development release.
- Read compatibility and write safety are separate. The adapter may read reviewed versions 13.55/13.59, but **writing now requires an exact `(version, executable SHA-256)` pair in an explicit write-qualification set**.
- The default ExifTool write-qualification set is empty because the sandbox still cannot transfer the genuine upstream full distributions for real read/write/read testing.
- Even a future qualified build remains fail-closed on ExifTool warnings/errors.
- A same-version executable whose bytes change is rejected for writing.

## Release implication
A version number such as “ExifTool 13.59” must never be treated as sufficient evidence that an arbitrary build is safe to modify HEIC/AVIF/RAW/Motion Photo/QuickTime metadata. Exact build qualification remains a release blocker.
