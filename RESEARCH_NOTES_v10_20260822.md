# Google Photos Archive Lab — Research Notes v10

Updated: 2026-08-22 JST

## ExifTool qualification tranche
- ExifTool candidate qualification and production promotion are separate preservation/security decisions. Passing a disposable JPEG lab test does not authorize writes to a personal archive.
- An exact candidate is identified by version plus executable SHA-256. When a complete distribution is supplied, a deterministic distribution-tree SHA-256 is recorded as additional provenance.
- The qualification harness rejects executable/distribution symlinks so provenance cannot silently point outside the declared candidate tree.
- A meaningful metadata-writer qualification must prove both sides of the operation: intended metadata changed/read back correctly, while the underlying visual/media payload did not change.
- JPEG whole-file SHA-256 is expected to change after metadata insertion, so a separate JPEG payload fingerprint is required to detect accidental recompression or visual alteration.
- Tool exit code or a printed success message is insufficient evidence. A no-op fake writer is an explicit failing regression case.
- The disposable JPEG qualification writes and reads back EXIF DateTimeOriginal, OffsetTimeOriginal, GPS, XMP Photoshop DateCreated and dc:description.
- Qualification reports use `gpa.exiftool-qualification.v1`, include harness ID `gpa.exiftool-jpeg-roundtrip.v1`, carry a stable qualification ID derived from tool/harness fingerprints, and are write-once.
- Candidate fingerprinting may include both executable SHA-256 and deterministic distribution-tree SHA-256.
- The production exact-build allow-list remains deliberately empty. No ExifTool build is currently authorized for embedded-media writes in a user archive.
- Next evidence target is genuine upstream ExifTool 13.55 production versus 13.59 development, followed by HEIC/HEIF, AVIF, MOV/MP4, Motion/Live Photo and representative RAW qualification.
