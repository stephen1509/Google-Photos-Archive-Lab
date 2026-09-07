# Google Photos Archive Lab — v52 real-world qualification workflow

v52 prepares the transition from synthetic/product qualification to genuine Windows and representative Google Takeout evidence without weakening any preservation gate.

## New operator workflow

- `gpa-lab prepare-real-world --session SESSION --output HANDOFF_DIR` creates an inert Windows handoff package. The generated qualification plan is bound to the exact session source SHA-256 values but does not copy Takeout ZIP bytes.
- `gpa-lab host-diagnostics --path PRIMARY --path BACKUP --report HOST_DIAGNOSTICS.json` captures read-only host/runtime/free-space diagnostics. A non-Windows run is explicitly non-qualifying.
- `RUN_WINDOWS_QUALIFICATION.ps1` is parameterized: it contains no personal Takeout paths or embedded source filenames. It runs product status, diagnostics, Windows core qualification, and optionally Windows writer review evidence.
- `gpa-lab privacy-support-bundle --session SESSION --report REPORT ... --output SUPPORT.zip` creates a sanitized troubleshooting package. Full local paths, source filenames, notes, and Takeout/media bytes are excluded. Exact source hashes are opt-in only.

## Safety boundaries

A handoff package is preparation, not evidence. Windows qualification is evidence, not permission to write. Writer review is evidence, not a production approval. Google retirement remains blocked until the complete independent exit gate passes.

## Representative Takeout intake

Use [`TAKEOUT_QUALIFICATION_HANDOFF.md`](TAKEOUT_QUALIFICATION_HANDOFF.md) when a representative export is available. The first real-Takeout pass is read-only: it fingerprints the supplied ZIP(s) before and after scanning and planning, writes only a new report/workspace selected by the operator, and fails if the source bytes change. It does not extract, rename, upload, modify, or delete a Takeout ZIP.

Motion Photo embedded writing is not part of this intake. The current real ExifTool evidence fails structural relationship verification after write, so Motion Photo material is preserved and surfaced for review while that lane remains blocked.
