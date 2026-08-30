# Google Photos Archive Lab — Research Notes v6

Updated: 2026-08-22 JST

## Validator-provenance tranche

- A SHA-256-clean copy proves that the bytes received from Google were preserved; it does **not** prove that the media is decodable or semantically healthy. Media preservation and media health are therefore separate archived facts.
- A `passed` media-validation claim is only useful long term if it records the exact validator and exact version/build evidence. The auditor now rejects passed claims with missing/placeholder provenance.
- Generic video validation cannot equate “ffprobe parsed the container” with “healthy video”. A valid audio-only MP4 is deliberately classified as failed for a media item expected to be video because it has no actual video stream.
- Common still images are validated read-only with Pillow. HEIC/HEIF/AVIF use libheif/heif-info when available. Recognized video families use ffprobe. RAW remains explicitly unavailable until a qualified RAW/ExifTool lane exists.
- Current lab tool versions verified in this environment: Pillow 12.3.0; ffprobe 7.1.5-0+deb13u1; libheif/heif-info 1.19.8.
- A corrupt/unsupported item is still preserved byte-for-byte. Validation failure or unavailability blocks the eventual Google-retirement gate rather than causing source data to be discarded.
- Migration completion and Google-retirement readiness remain separate concepts: a migration can correctly preserve everything received even if some media cannot yet be independently validated.

## Preservation discipline

- v6 was rebuilt from the last fully saved v5 checkpoint (300/300) rather than relying on higher historical counts that were not backed by a recoverable source checkpoint.
- v6 reached 310/310 and was then zipped, SHA-256 hashed, ZIP-integrity tested, extracted into a fresh directory outside the working tree, and the full suite rerun there successfully.
- The checkpoint ZIP is preserved in Dropbox as base64 text because the available Dropbox connector does not expose a byte-preserving binary upload action. The checkpoint manifest contains the exact binary SHA-256 and reconstruction command.
