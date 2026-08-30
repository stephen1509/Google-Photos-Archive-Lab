# Google Photos Archive Lab — Project Authority (Current)

Updated: 2026-08-22 JST

## Mission
Build a local-first, Google-independent reconstruction and archival application that consumes one or more raw Google Photos Takeout ZIP archives and produces a durable, searchable personal media archive on user-controlled storage.

## Governing rules
1. Preserve first. Never modify the original Takeout ZIPs.
2. Never guess. Unknown/conflicting chronology or location remains unknown/conflicted until evidence or user confirmation resolves it.
3. Preserve original media bytes unless an embedded-metadata writer has been independently qualified for that exact format/toolchain and media payload integrity is verified.
4. Keep original camera facts, Google-current facts, derived facts, probable suggestions, and user-confirmed facts separate with provenance.
5. Final folder placement is primarily `Photos/YYYY/MM - Month`; uncertainty that crosses a folder boundary goes to `_Needs Placement`.
6. Partial dates remain partial. Never fabricate day/time/timezone/GPS to satisfy a file format or folder rule.
7. Important metadata must remain portable outside the application/database (XMP/standard metadata plus open GPA JSON records).
8. SQLite/catalogue data is rebuildable and is never the sole authoritative copy.
9. Exact duplicate bytes, logical Google Photos items, variants/families, and Takeout occurrences are different concepts and must not be conflated.
10. Every mutation is transactional/auditable/resumable; never silently overwrite unrelated destination content.
11. Unknown/unsupported Takeout material is preserved byte-for-byte and surfaced for review.
12. Google retirement is recommended only after source completeness evidence, archive audit, media validation, raw-source preservation, and independently hash-verified redundant copies on separate physical media satisfy the retirement gate.
13. Meaningful development milestones must be rerunnable from a saved checkpoint before their test count is reported as authoritative.

## Current implementation authority
The newest clean-room-tested checkpoint supersedes earlier checkpoints. `CURRENT_STATUS.md` / `STATUS.md` records the current verified test count and known blockers.
