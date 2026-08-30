# Google Photos Archive Lab — Research Notes v17

Updated: 2026-08-22 JST

This versioned Dropbox record adds the v17 findings to the previously preserved cumulative research. The full cumulative v17 research file is also stored in the persistent project Library at `/Projects/Google Photos Archive Lab/RESEARCH_NOTES_v17_20260822.md`.

## v17 — Backup link isolation and per-record Source Vault binding

Hash equality alone is not enough to prove that a redundant copy is physically independent. A filesystem symlink—and, on Windows, a junction/reparse-style link—can make a path inside an apparent backup tree resolve to bytes elsewhere. In the worst case a mirror object could resolve back to the primary archive and still return the correct SHA-256. Therefore redundant-copy tree-manifest creation, mirror verification, and mirror synchronization must fail closed on link-like expected paths. A link-backed file must never satisfy `redundant_copy_verified` merely because its target bytes match.

Source Vault audit must distinguish between a byte object checked once and every provenance record being valid. The previous audit cached verification by vaulted path; a later occurrence record pointing at an already-checked path could claim a different digest without independently establishing that record-to-byte binding. v17 caches an exact `(size, SHA-256)` binding instead. Every additional occurrence record must agree with that binding before its digest is admitted to the verified source set.

A second crash-recovery edge case was closed. If a vault already had valid records, a later import could crash after a new raw ZIP byte object became durable but before its occurrence record was written. Auditing only existing records could leave that new byte object invisible. The audit now inventories the archive-object tree and flags unreferenced byte objects as orphans. A normal rerun still heals the state by reusing the exact committed bytes and creating the missing occurrence record.

Six adversarial automated regressions now cover source-tree symlinks, hash-matching destination symlinks, fail-before-copy behavior, orphan byte objects, conflicting duplicate provenance records, and link-backed Source Vault objects. The existing 101-object `gpa.backup-fault-recovery.v1` qualification still passes after these changes.

## Current ExifTool research position

Upstream ExifTool version history was rechecked on 2026-08-22. It identifies 13.55 (2026-04-07) as the most recent production release. Version 13.59 (2026-05-27) is a later development release and includes a security update plus stricter behavior that promotes some potentially lossy XMP-write conditions from warnings to errors.

The project therefore still requires exact candidate-byte qualification for both 13.55 and 13.59. Candidate qualification and production promotion remain separate. No ExifTool build is currently authorized for production embedded-media writes.

## Persistent v17 evidence

- Reproducible suite: 354 passed, 0 failed.
- Clean-extraction suite: 354 passed, 0 failed.
- Checkpoint SHA-256: `f766145e38699910e6f7c87553c4a356897fc12ad8be360838649f8dad50a3f5`.
- Persistent checkpoint: `/Projects/Google Photos Archive Lab/Checkpoints/google_photos_archive_lab_checkpoint_20260822_v17.zip`.
