# Google Photos Archive Lab — Product workflow

This checkpoint adds a product-facing orchestration layer around the preservation engine. The engine remains fail-closed and source ZIPs remain preservation inputs that must not be modified.

## Product workflow
1. `gpa-lab product-init` creates a write-once session baseline and SHA-256 fingerprints every selected Takeout ZIP.
2. `gpa-lab qualify-real-takeout` performs CRC/ZIP inspection, tolerant member indexing, classification, read-only planning, source-completeness checks, and a before/after source fingerprint comparison. It writes a machine-readable report without extracting or modifying source ZIPs.
3. `gpa-lab product-set` attaches durable evidence paths and explicit confirmations to the session. It supports reversal of confirmations and atomic session replacement.
4. Existing `preview`, `run`, `vault`, `receipt`, `exit-evidence`, `audit`, Windows qualification, and `verify` commands remain the preservation primitives.
5. `gpa-lab product-status` recomputes the dashboard from live evidence. It never treats migration success as permission to retire Google originals.
6. `gpa-lab desktop --session ...` launches the thin Tk desktop dashboard. Preservation and gate logic remain outside the GUI so they can be tested headlessly.

## Retirement gate
The product dashboard remains `NOT_READY` unless all required gates pass: exact source fingerprint integrity, a ready read-only preview, archive audit, migration verification (including Takeout receipts/exit checklist/source-vault/redundancy/media validation), representative real-Takeout qualification bound to the exact session source hashes, real Windows core qualification, and exact Windows writer review qualification. Apple Photos acceptance is an additional gate when the session target requires it.

Passing writer qualification is evidence only. It does not populate production approval allow-lists, enable embedded production metadata writes, or authorize Google deletion by itself.

## Still requires real external evidence
This container cannot produce genuine Windows storage/device evidence, real ExifTool-on-Windows evidence, Apple Photos acceptance evidence, independent physical-media confirmation, or representative personal Google Takeout evidence. Those gates intentionally remain blocked until their exact reports exist.
