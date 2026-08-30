# Google Photos Archive Lab — Research Notes v16 Addendum

Updated: 2026-08-22 JST

## Source Vault crash boundary
The Source Vault already stored raw Takeout ZIPs by SHA-256 and separated immutable byte objects from occurrence provenance records, but the crash boundary between those commits had not been explicitly qualified. v16 adds a qualification-only callback immediately after verified byte-object durability and before the occurrence record. A simulated process loss at this exact point leaves the raw ZIP bytes intact while the vault audit remains incomplete. A normal rerun verifies and reuses those exact bytes, creates the missing occurrence record, and returns the vault to a clean state without duplicating the raw archive.

## Redundant mirror recovery
The previous mirror layer could build and verify a tree manifest but did not itself provide a transactional/resumable copy operation. v16 adds `synchronize_mirror()`. It validates the manifest, rejects unsafe/path-traversal and duplicate paths, preflights every primary source file against the expected size/SHA-256, and preflights every existing backup destination before making any change. Only after all checks pass does it copy missing files using the existing staged + fsync + no-overwrite transaction primitives.

This design means:
- interrupted copy can be rerun safely;
- exact existing mirror files are accepted;
- missing files are filled;
- conflicting destination bytes fail closed and are never silently overwritten;
- extras are not implicitly deleted;
- a late destination conflict cannot leave an additional partially advanced backup because all existing destinations are checked before new commits.

## Qualification evidence
The `gpa.backup-fault-recovery.v1` stress harness qualified a 101-object mirror (100 payload files plus one metadata file). A deliberate interruption after 4 committed objects was detected as 97 missing. A normal rerun converged to 101/101 verified files. A deliberately corrupted destination blocked synchronization before another missing file was written.

The Source Vault interruption test simulated power loss after raw ZIP byte commit and before provenance record creation. On restart, the exact ZIP object was reused rather than duplicated, and the vault converged to 1 archive / 1 provenance record / 0 audit problems.

## Result
Five new automated tests were added. Full source-tree suite: 348 passed, 0 failed. The packaged v16 checkpoint was hash verified, ZIP-integrity tested, extracted into a fresh directory, compiled, and rerun: 348 passed, 0 failed.

The specific v15 blocker for Source Vault / mirror fault-injection qualification is therefore closed. Real separate-device qualification and larger real-media/video backup stress remain outstanding.
