# Current status — v52 candidate

The preservation engine remains fail-closed. v52 prepares genuine external evidence collection with a parameterized Windows PowerShell runner, exact session-source binding, read-only host diagnostics, and a privacy-safe troubleshooting bundle.

No Windows evidence is manufactured by handoff generation. Non-Windows diagnostics cannot pass the Windows handoff gate. Support bundles exclude Takeout/media bytes, full source paths, filenames, notes and free-form diagnostic text; source hashes are opt-in.

Production ExifTool approvals remain empty. Embedded production metadata writing remains disabled. RAW embedded rewriting remains prohibited. Google originals cannot be retired without the full independent evidence chain.

## Windows offline readiness update — superseded 2026-09-08

- The prior “671 passed” prose claim is not supported by a matching committed
  complete-suite/JUnit artifact and is **not current qualification evidence**.
  Historical files are retained; see `QUALIFICATION_EVIDENCE_RECONCILIATION_20260908.md`.
- Windows x64 host diagnostics, Windows core storage/RAW evidence, HEIC decode qualification, base media format qualifications, and Live Photo synthetic relationship qualification have passed in their recorded scopes.
- The admitted ExifTool 13.59 distribution remains pinned by qualification evidence. It is not treated as a global-PATH production approval.
- Motion Photo embedded writer qualification remains blocked after real ExifTool writes failed structural relationship verification. Preserve those sources byte-for-byte and surface them for review.
- No real Takeout ZIP, personal media, upload, production metadata write, or Google deletion was performed in this offline phase.

## Windows-qualified software checkpoint authority — 2026-09-08

This append-only authority record supersedes the 20260830 v52 ZIP **for
software restoration only**.  The old checkpoint remains retained as historical
authority and must not be overwritten or deleted.

- Checkpoint: `google_photos_archive_lab_checkpoint_20260908_v52_windows_qualified_112b287.zip`
- SHA-256: `9702C73B85E5EF07949E26E45FE20C5C179808D88A9BC48D5ACBF545E18912DE`
- Immutable source commit archived: `112b287e81b7527618d7a2ebf1ae3e5505822dbb`
- Archive integrity and a fresh extraction were verified on Windows/Python
  3.12.14.  The extracted source completed 671 tests with 0 failures, 0
  errors, 15 documented skips, and 0 timeouts.

This is a software-only gate closure.  It does not authorize reading a real
Takeout, accessing personal media, metadata writing, RAW rewriting,
reconstruction, upload, or Google deletion.  See
`CHECKPOINT_20260908_V52_WINDOWS_QUALIFIED.md` and
`QUALIFICATION_EVIDENCE_RECONCILIATION_20260908.md` for provenance and limits.
