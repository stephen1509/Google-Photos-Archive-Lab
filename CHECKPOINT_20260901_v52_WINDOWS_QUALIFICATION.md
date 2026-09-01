# v52 Windows qualification checkpoint — 2026-09-01

Dropbox remains the authoritative working source.  GitHub is a source-and-evidence
checkpoint only; it is not the authority for restoring or changing the project.

## Authority base

- `google_photos_archive_lab_checkpoint_20260830_v52.zip`
- SHA-256: `3714bf5dbededf9cdf37b006cb18b3eaf491fdbc60c9a37fb8a9a087b0412973`
- Extracted working source: `gpa_v52_windows_qualification/gpa_v52_work`

## Windows evidence collected

- AVIF synthetic decode/relationship toolchain passed with the explicit,
  fingerprinted libheif 1.23.1 AOM decoder.
- ExifTool 13.59 Windows x64 archive was admitted with its pinned size and SHA-256.
- The base synthetic JPEG ExifTool qualification passed.  It is evidence only;
  production metadata writing and Google retirement remain disabled.

## Checkpoint exclusions

The Git checkpoint intentionally excludes re-downloadable ExifTool archives,
prepared binary distributions, synthetic work folders, and interpreter caches.
Their immutable admission/qualification reports are retained in the working source.
