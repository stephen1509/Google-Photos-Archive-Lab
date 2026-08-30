# Google Photos Archive Lab — Checkpoint v6

Updated: 2026-08-22 JST

## Verified state
- 310 automated tests passed.
- 0 failed.
- 1 intentional adversarial duplicate-ZIP-name warning was emitted by the test corpus.
- Python compile pass succeeded.
- Checkpoint ZIP integrity tested successfully.
- Full suite rerun successfully from a fresh extraction outside the development tree.

## Binary checkpoint identity
Original filename:
`google_photos_archive_lab_checkpoint_20260822_v6.zip`

SHA-256:
`f9069ffa108bb453f423a482fd58745a27fd8584b420541bbdfc7f8d3723ee7c`

Original ZIP size: 144140 bytes.

Dropbox source snapshot:
`SOURCE_CHECKPOINT_20260822_v6.zip.b64`

## Reconstruct the binary ZIP from Dropbox
On a system with GNU/coreutils base64:

```bash
base64 -d SOURCE_CHECKPOINT_20260822_v6.zip.b64 > google_photos_archive_lab_checkpoint_20260822_v6.zip
sha256sum google_photos_archive_lab_checkpoint_20260822_v6.zip
```

The resulting SHA-256 must exactly equal:
`f9069ffa108bb453f423a482fd58745a27fd8584b420541bbdfc7f8d3723ee7c`

Do not use a reconstructed checkpoint if the hash differs.

## Authority rule
This v6 source/test checkpoint is a durable reproducible milestone. Historical higher test counts that are not backed by an independently restorable checkpoint do not supersede it.
