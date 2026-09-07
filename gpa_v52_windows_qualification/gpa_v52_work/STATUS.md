# Current status — v52 candidate

The preservation engine remains fail-closed. v52 prepares genuine external evidence collection with a parameterized Windows PowerShell runner, exact session-source binding, read-only host diagnostics, and a privacy-safe troubleshooting bundle.

No Windows evidence is manufactured by handoff generation. Non-Windows diagnostics cannot pass the Windows handoff gate. Support bundles exclude Takeout/media bytes, full source paths, filenames, notes and free-form diagnostic text; source hashes are opt-in.

Production ExifTool approvals remain empty. Embedded production metadata writing remains disabled. RAW embedded rewriting remains prohibited. Google originals cannot be retired without the full independent evidence chain.

## Windows offline readiness update — 2026-09-08

- Full local regression from a short disposable Windows test path: **671 passed, 1 expected duplicate-ZIP-name warning**.
- Windows x64 host diagnostics, Windows core storage/RAW evidence, HEIC decode qualification, base media format qualifications, and Live Photo synthetic relationship qualification have passed in their recorded scopes.
- The admitted ExifTool 13.59 distribution remains pinned by qualification evidence. It is not treated as a global-PATH production approval.
- Motion Photo embedded writer qualification remains blocked after real ExifTool writes failed structural relationship verification. Preserve those sources byte-for-byte and surface them for review.
- No real Takeout ZIP, personal media, upload, production metadata write, or Google deletion was performed in this offline phase.
