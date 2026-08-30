# Google Photos Archive Lab — Status v6

Updated: 2026-08-22 JST

## Reproducible milestone
- **310 passed, 1 intentional adversarial duplicate-ZIP-name warning, 0 failed**.
- Python compile pass succeeds.
- Checkpoint ZIP integrity verified and full suite rerun from a fresh extraction outside the project tree.
- Authority was deliberately restored from the last fully saved v5 checkpoint (300/300) before this tranche; unsaved historical test counts are not treated as authoritative.

## New in v6
- Added read-only media/container validation as a separate fact from byte-preservation integrity.
- Common still images validate with Pillow and record the exact Pillow version.
- Recognized video families validate with ffprobe and require at least one actual video stream; a parseable audio-only MP4 fails video validation.
- HEIC/HEIF/AVIF validate with libheif/heif-info when available and record exact libheif version.
- RAW validation fails closed as unavailable until a qualified RAW/ExifTool lane exists.
- Immutable GPA asset revisions now record media-validation status, validator, version, detail, and video-stream count where relevant.
- Independent audit rejects any `passed` media-validation claim lacking named validator + non-placeholder version provenance.
- Google-retirement verification has a separate qualified-media-validation gate. A migration may complete because the received bytes were preserved even when media validation fails or is unavailable; cloud retirement remains blocked.

## Current local validator versions
- Pillow 12.3.0
- ffprobe 7.1.5-0+deb13u1
- libheif/heif-info 1.19.8

## Safety position
Do **not** delete Google originals based on this development build. Embedded metadata rewriting remains disabled pending exact ExifTool qualification, RAW validation remains incomplete, and representative real Takeout samples are still required before release qualification.
