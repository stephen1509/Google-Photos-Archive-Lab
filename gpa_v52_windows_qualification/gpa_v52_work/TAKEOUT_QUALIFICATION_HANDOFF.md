# Representative Google Takeout qualification — operator handoff

This is the next step only after Stephen explicitly provides the selected Google Takeout ZIP path(s). It is a qualification pass, not a reconstruction run and not permission to delete anything from Google Photos.

## Before starting

1. Keep every selected Takeout ZIP exactly where Google downloaded it. Do not extract, rename, repair, recompress, or edit it.
2. Keep all parts from the same export together. If the Takeout UI/email states an expected part count, retain that information; it is needed for the durable receipt/completeness gate.
3. Provide the exact ZIP path(s), an empty GPA workspace/report location, and the intended primary and independent backup destinations. GPA will not scan nearby folders to look for personal files.
4. Do not put a Takeout ZIP inside the reconstructed-archive destination or a folder that a later cleanup policy could target.

## What GPA will do first

1. SHA-256 fingerprint each explicitly supplied ZIP, record size/member counts, and test ZIP readability.
2. Perform read-only source inventory and planning. It recognizes known media and sidecar material, and routes unsupported, conflicting, or ambiguous material to review rather than guessing.
3. SHA-256 fingerprint the supplied ZIPs again. Any byte change fails the qualification.
4. Write a new, no-overwrite qualification report and update only explicitly selected GPA workspace/evidence files.
5. Run the already-qualified Windows host/storage checks. It does not enable production metadata writing, RAW rewriting, reconstruction, or Google deletion.

The first pass does **not** copy, extract, upload, modify, or delete Takeout/media bytes. It does not enable production metadata writing or Google retirement.

## Expected resources

- Disk: the read-only qualification itself writes only small reports/workspace metadata. Reserve separate empty destinations for later reconstruction and redundant copies; do not treat the free space needed for this first pass as an archive-capacity estimate.
- Disk reads: at least two full sequential reads of each supplied ZIP for the before/after SHA-256 fingerprints, plus ZIP-directory and planning reads. For a total export size `S`, budget roughly `2 × S` of source reads, with additional metadata reads depending on the export.
- CPU: SHA-256 hashing and ZIP/JSON planning; this is normally I/O-bound. No video transcoding, image recompression, or RAW rewriting is performed.
- Network: none. The qualification does not upload Takeout contents.

## Current Windows readiness (2026-09-08)

- Windows x64 host diagnostics passed with C: and D: accessible.
- Available free space at the latest check: about 681 GB on C: and 2.29 TB on D:.
- Python 3.14.4, FFmpeg/ffprobe, ExifTool 13.59 (qualified distribution), and the locally rebuilt HEIC decoder lane are available for their existing qualification scopes.
- Motion Photo writer qualification is **blocked**. The real ExifTool path altered Motion Photo structure during synthetic relationship testing; GPA must preserve such sources byte-for-byte and surface them for review.

## Evidence and stop conditions

Stop and report rather than continuing if a ZIP is unreadable, its before/after hash differs, expected Takeout parts are missing, a source is ambiguous/unsupported, the output destination is unsafe, or the planned operation would require an unapproved write.

A successful read-only qualification is still not archive completion. Google originals remain in place until all required source-vault, reconstruction/audit, media-validation, redundancy, Windows/toolchain, completeness, and exit-evidence gates are independently satisfied.
