# Google Photos Archive Lab — Research Notes v18

Updated: 2026-08-22 JST

This file records the v18 research/development delta. The complete cumulative research record is preserved in the persistent Library at `/Projects/Google Photos Archive Lab/RESEARCH_NOTES_v18_20260822.md`.

## v17 backup/source-vault findings retained
- Hash equality alone does not prove that a redundant copy is physically independent. A filesystem symlink, Windows junction, or other reparse-style link can make an apparent backup resolve to primary bytes while still returning the expected SHA-256.
- Tree-manifest creation, mirror verification and mirror synchronization therefore fail closed on link-like expected paths.
- Source Vault auditing must bind every occurrence record to the exact verified `(size, SHA-256)` byte object. A duplicate/conflicting record cannot inject an unverified digest merely because another record already verified the same path.
- Auditing must inventory vaulted byte objects as well as records so a post-byte-commit/pre-record crash cannot leave an invisible orphan object.

## v18 authority reconciliation
A preservation audit found two non-overlapping development lines. The preserved v17 checkpoint contained stronger Source Vault/mirror link and provenance protections. A later v16 working line contained bounded ExifTool subprocess handling and a hung-tool regression. v18 was built from independently reproduced v17 and merged only the missing external-tool boundedness changes, preserving both safety lines.

## Bounded external tools
An external metadata helper must not make migration or qualification non-terminating. ExifTool version checks, metadata reads and metadata writes now use positive configurable subprocess timeouts and fail closed. Candidate qualification has its own bounded subprocess budget. A deliberately hung fake candidate is a regression case and must fail qualification.

This timeout work does **not** authorize production embedded writes. Candidate qualification and production promotion remain separate, exact-build decisions. The production ExifTool allow-list remains empty.

## Backup fault qualification
The `gpa.backup-fault-recovery.v1` harness was rerun after reconciliation:
- 101 expected mirror objects;
- deliberate interruption after 4 commits;
- 97 missing objects correctly detected;
- normal rerun healed to 101/101 verified;
- a conflicting destination failed preflight before additional backup mutation;
- Source Vault post-byte-commit interruption recovered by exact-byte reuse;
- final Source Vault audit clean.

## Release-gate observation
The complete automated suite is run as bounded fresh pytest modules. Some orchestration environments impose an aggregate shell-call wall-clock limit that can be shorter than the legitimate sum of all bounded module runtimes. Therefore an authoritative milestone is based on executing every module and reproducing the entire test count from a fresh checkpoint extraction, not on one hosting shell command being allowed unlimited wall-clock time.

## v18 verified milestone
- Final clean-extraction suite: **355 passed, 0 failed**.
- Checkpoint SHA-256: `6e0104dc0655e7a91128fe7f4f30008950595fbf5d6e26777f1d252578a83329`.
- Embedded metadata writing remains disabled.
- Google retirement remains blocked pending all source-completeness, format/tool qualification, representative Takeout, independent-physical-backup, Windows-device and exit-evidence gates.
