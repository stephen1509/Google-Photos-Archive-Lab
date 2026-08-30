# Google Photos Archive Lab — Status v9

Updated: 2026-08-22 JST

## Reproducible milestone
- **324 passed, 0 failed**.
- One intentional adversarial duplicate-ZIP-name warning from the test corpus.
- Python compile pass succeeds.
- Checkpoint ZIP integrity verified.
- Full suite rerun successfully from a fresh extraction outside the development tree.

## New in v9
- Frozen archive format `gpa.archive.v1` with manifest schema `gpa.archive-manifest.v1`.
- New empty destinations receive version state only when execution begins after preflight; preview/preflight stays read-only.
- Unknown future formats, unknown manifest schemas, and non-empty foreign destinations fail closed before mutation.
- Recognizable legacy GPA archives require explicit metadata-only upgrade before further mutation.
- Legacy upgrade records deterministic pre-upgrade tree SHA-256/file-count evidence and does not rewrite existing media/metadata.
- Legacy upgrades are idempotent.
- Symlinked archive-format manifests and symlinked legacy files are rejected.
- Independent audit records archive-format support.
- Google-retirement verification requires a supported versioned archive format by default; explicit opt-out is only for legacy analysis.
- CLI adds `gpa-lab archive-format --output ... [--upgrade]`.

## Safety position
Do not delete Google originals based on this development build. Embedded metadata rewriting remains disabled pending exact ExifTool qualification; RAW qualification and representative real Takeout testing remain incomplete.
