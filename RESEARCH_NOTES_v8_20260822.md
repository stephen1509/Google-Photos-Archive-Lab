# Research Notes v8 — 2026-08-22

## ExifTool qualification harness
- Added `gpa.exiftool-qualification.v1` to turn writer qualification into a repeatable lab process rather than an ad-hoc manual decision.
- A candidate executable is fingerprinted first. The harness temporarily permits only that exact `(version, SHA-256)` pair inside the isolated qualification workspace; passing does **not** automatically promote the build to the production write allow-list.
- Current JPEG qualification requirements: whole-file bytes must change; entropy-coded visual payload must remain byte-identical; output must still decode; capture date, GPS and description must read back correctly.
- A fake executable that only claims success without modifying the file is rejected, proving the harness does not trust command exit status/output alone.
- CLI: `gpa-lab qualify-exiftool --executable <path> --workdir <dir> --report <json>`.

## Next qualification expansion
When genuine upstream distributions can be transferred into the lab, run 13.55 production and 13.59 development through this harness, then add PNG, AVIF, HEIC/HEIF, MOV/MP4 and dedicated Android Motion Photo cases. RAW writing will likely remain sidecar-first even if ExifTool can technically write some RAW formats; technical capability is not sufficient justification to mutate irreplaceable RAW originals.
