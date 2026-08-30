# Google Photos Archive Lab — Research Notes v9

Updated: 2026-08-22 JST

## Archive-format/versioning tranche
- A long-lived personal archive needs an explicit format identity independent of the application/database. `gpa.archive.v1` is now frozen as the first archive-format identifier, described by `gpa.archive-manifest.v1`.
- Old software must fail closed on unknown future formats rather than guessing how to mutate them.
- Preview/storage-preflight must remain truly read-only. Archive-format initialization occurs only after execution preflight succeeds and immediately before archive mutation.
- Recognizable legacy GPA archives can be upgraded explicitly without touching existing media/metadata. The upgrade records deterministic pre-upgrade file-count and SHA-256 tree evidence.
- A non-empty unrelated destination is never silently adopted as a GPA archive.
- Archive-format manifest and upgrade paths reject symlinks; the upgrade must not follow files outside the archive root.
- Independent audit records format support, and the final Google-retirement verifier requires a supported archive format by default. Legacy forensic analysis may explicitly opt out without weakening the normal retirement gate.
