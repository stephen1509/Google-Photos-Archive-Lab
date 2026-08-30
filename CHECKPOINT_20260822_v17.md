# Google Photos Archive Lab — Checkpoint v17

Updated: 2026-08-22 JST

## Verified state
- **354 automated tests passed**.
- 0 failed.
- One intentional adversarial duplicate-ZIP-name warning from the test corpus.
- Python compile pass succeeded.
- ZIP integrity test succeeded.
- Full test suite rerun from a fresh extraction outside the development tree: **354 passed**.

## v17 delta
- Symlink/junction isolation for redundant-copy manifests, verification and synchronization.
- Backup verification can no longer count link-backed target bytes as independent copies.
- Per-occurrence Source Vault records are bound to exact verified size/SHA-256 byte objects.
- Orphan vaulted byte objects after interrupted provenance commits are detected.
- Link-backed Source Vault records/archive objects fail closed.
- Six new adversarial automated regressions.

## Stress evidence
- Existing `gpa.backup-fault-recovery.v1`: PASS.
- 101 expected mirror objects; crash after 4 commits left 97 missing.
- Healing rerun converged 101/101.
- Corrupt destination blocked before further write.
- Source Vault crash/restart converged to clean exact-byte state.

## Binary checkpoint
Persistent Library path:
`/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v17.zip`

Size: 168376 bytes

SHA-256: `f766145e38699910e6f7c87553c4a356897fc12ad8be360838649f8dad50a3f5`

## Authority
v17 supersedes v16 after independent clean extraction/reproduction. Production ExifTool embedded-media writing remains disabled.
