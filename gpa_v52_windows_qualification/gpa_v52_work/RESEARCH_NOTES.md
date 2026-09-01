# Research Notes — Key External Findings

Date: 2026-08-21 JST

- Google Photos Incremental Takeout (announced June 2026): the first scheduled export is full; later exports can include items uploaded, created or edited since the previous successful export. Therefore identical media bytes may legitimately reappear with changed metadata and require a revision/relocation rather than duplication.
- Google Takeout post-2024 sidecar naming can use `.supplemental-metadata.json`, with observed deterministic 51-character total filename truncation preserving `.json`, plus duplicate `(N)` marker relocation in some cases.
- Sidecar/media files and album metadata/media may be split across separate Takeout ZIP parts. Filename/title coincidence across different logical folders is not sufficient pairing evidence.
- Android Motion Photo 1.0: `MotionPhoto=1` is not definitive; an actual video payload must be structurally present. Container-item metadata can identify Primary/GainMap/MotionPhoto ordering; HEIC/AVIF Motion Photos use terminal `mpvd` video data. Legacy MicroVideoOffset is a separate compatibility lane.
- Apple Live Photos are paired by matching content identifiers (still and movie), not by filename similarity. A proven pair can share placement context without making their capture timestamps identical facts.
- Adobe XMP Date supports reduced precision (`YYYY`, `YYYY-MM`) and explicitly treats a missing timezone as unknown.
- Capture-time semantics distinguish `XMP-photoshop:DateCreated` / EXIF `DateTimeOriginal` from XMP resource/digitization creation time.
- Adobe EXIF-for-XMP defines `exif:GPSLatitude` and `exif:GPSLongitude` as text properties. ExifTool documents XMP coordinates as carrying hemisphere in the value (or signed input), unlike EXIF's separate Ref tags.
- ExifTool 13.49 fixed Google Photos display problems after HEIC Motion Photo writes. 13.59 adds security changes and promotes some potentially lossy XMP-write conditions from warnings to errors. The project therefore pins/qualifies versions rather than invoking an arbitrary installed build.
- timezone boundary lookup and historical UTC-offset lookup are distinct problems: a boundary dataset determines the zone name for coordinates, while IANA timezone rules determine historical offset for a date. Current-only boundaries must not be silently treated as historically authoritative.
- GeoNames or equivalent downloadable gazetteers can support offline place-name suggestions. Derived names are enrichment/provenance, not substitutes for exact GPS.
- Existing Immich/immich-go, PhotoPrism, PhotoStructure, Metadata Fixer and related projects provide useful patterns and bug reports, but no one tool is treated as authoritative. Reported Takeout failure cases are converted into regression requirements where possible.
- Independent post-migration verification is necessary before deleting a cloud source: immutable metadata revisions alone are insufficient unless current media, portable XMP, preserved unknown files and content-addressed historical blobs can all be audited.

## Additions from the v4 development tranche
- ExifTool upstream currently distinguishes **13.55 as the most recent production release** and **13.59 as a development release**. The project should qualify both; “latest” is not automatically the bundling choice.
- A corrected scalability benchmark must use unique media bytes. An earlier synthetic benchmark accidentally collapsed identical bytes by SHA-256, correctly producing one logical asset and therefore hiding per-asset metadata costs.
- Per-photo ZIP reopening was confirmed to be catastrophic: 3,000 unique stills exceeded a two-minute benchmark timeout. Archive-batched embedded inspection reduced the same 3,000 distinct-media planning workload to ~0.79 s here.
- With real parsable EXIF, 1,000 small JPEGs carrying DateTimeOriginal were inspected and placed in ~0.19 s here. These are algorithmic lab measurements, not disk-speed promises.
- GeoNames country/admin code files are needed in addition to city points if labels should read naturally (for example `Nara, Japan` rather than `Nara, 29, JP`). Exact GPS remains the fact; names remain derived enrichment.
- Historical timezone provenance is now carried into normal planning. A boundary product’s temporal scope determines whether it may upgrade chronology or only suggest a likely local time.
- Review context based on surrounding numeric camera filenames can be useful to a person, but filename sequence is not archival proof. It is therefore represented only as a probable suggestion with the exact neighboring evidence shown.
- User corrections are now append-only decisions. The effective projection may change, but the program retains previous decisions and all original Google/camera evidence.

## Additions after the 2026-08-21 v4 checkpoint
- Exact media-byte equality (SHA-256) is **content identity**, not proof of one logical Google Photos item. Takeout URLs are useful identity hints but are undocumented as stable permanent IDs. The archive therefore keeps an immutable cross-run identity observation ledger separate from current metadata revisions.
- One URL hint observed with multiple material metadata signatures is consistent with one probable logical item whose Google-side metadata evolved; multiple distinct URL hints for identical bytes are preserved as probable multiplicity requiring review.
- A current searchable catalogue must never merge stale metadata from older revisions into the current description/date/location merely to preserve identity history. Identity multiplicity is exposed separately.
- Portable metadata for a content-level file should be based on field consensus. Agreed descriptions may be written even when volatile raw Google JSON differs; disputed item-level descriptions should remain in archival records rather than being forced into one XMP projection.
- Offline third-party datasets are part of archival provenance and require their own supply-chain controls: role, version, source, license, attribution, temporal scope, exact file inventory and SHA-256.
- Timezone-boundary version labels alone are insufficient provenance: the exact verified pack hash must travel with the derived timezone evidence.

## Additions from the 2026-08-22 validator-provenance tranche
- A successful SHA-256 copy proves faithful preservation of source bytes, not that the media container is decodable or semantically healthy. Media validation is therefore a separate archived fact.
- Validator provenance must include both tool identity and exact version. A bare `passed` flag is not sufficient long-term evidence.
- Generic video validation requires at least one actual video stream; ffprobe successfully parsing an audio-only `.mp4` is not enough to call it a healthy video asset.
- Current environment qualification: Pillow 12.3.0, ffprobe 7.1.5-0+deb13u1, and libheif/heif-info 1.19.8 are available for read-only validation.
- RAW validation remains deliberately unavailable until a qualified RAW/ExifTool path is tested. This must block a final Google-retirement recommendation, but it does not invalidate byte-for-byte preservation.

## Additions from the 2026-08-22 archive-format/versioning tranche
- A long-lived personal archive needs an explicit format identity independent of the application/database. `gpa.archive.v1` is now frozen as the first archive-format identifier, described by `gpa.archive-manifest.v1`.
- Unknown future formats fail closed: old software must not guess how to mutate an archive created by a newer incompatible version.
- Preview/storage preflight must stay truly read-only; archive-format initialization occurs only after execution preflight succeeds and immediately before archive mutation.
- A recognizable older GPA archive can be upgraded explicitly without touching existing media/metadata. The upgrade records deterministic pre-upgrade tree evidence so the manifest itself documents the pre-migration state.
- A non-empty unrelated destination is never automatically adopted as a GPA archive.
- Archive-format upgrade/audit paths treat symlinks as unsafe; they must not hash or trust files outside the archive root through symlink traversal.
- Google-retirement verification now treats supported archive-format versioning as a separate mandatory gate, while legacy forensic/analysis calls may explicitly opt out.

## Additions from the 2026-08-22 ExifTool-qualification tranche (v10)
- ExifTool *candidate qualification* and *production promotion* are separate security/preservation decisions. Passing one disposable JPEG lab test does not authorize writes to a personal archive.
- An exact candidate is identified by version plus executable SHA-256. When a complete distribution is supplied, a deterministic distribution-tree SHA-256 is recorded as additional provenance.
- The qualification harness rejects executable/distribution symlinks so provenance cannot silently point outside the declared candidate tree.
- A meaningful metadata-writer qualification must prove both sides of the operation: intended metadata changed/read back correctly, while the underlying visual/media payload did not change.
- For JPEG, whole-file SHA-256 is expected to change after metadata insertion, so a separate JPEG payload fingerprint is required to detect accidental recompression/visual alteration.
- A tool exit code or “success” message is insufficient evidence. A no-op fake writer is an explicit failing regression case.
- Qualification reports use `gpa.exiftool-qualification.v1`, carry a stable qualification ID derived from the harness/tool fingerprints, and are write-once so later runs cannot silently replace evidence.
- The production exact-build allow-list is intentionally empty. No ExifTool build is currently authorized for embedded-media writes in a user archive.

## v11 — Validator evidence binding (2026-08-22)

A media-validator version string is not sufficient long-term provenance. Two binaries can report the same version while differing in build/patch state, and a validator that is intended to be read-only should not be trusted merely because its command name implies read-only behavior.

v11 therefore binds every successful media-validation decision to:
- the exact validator implementation SHA-256,
- the exact media SHA-256 that was validated,
- an explicit `bytes_unchanged=true` result.

External validator executables are hashed directly. Pillow receives a deterministic local build fingerprint derived from the exact loaded validation code/native core. During normal materialization the source media SHA-256 is already known from Takeout extraction, so the validator only needs to prove the post-validation file still matches that hash. Standalone validation hashes before and after.

This keeps media preservation and media health conceptually separate while also proving that the health check did not mutate the preserved object.

ExifTool release research remains unchanged: upstream identifies 13.55 as the most recent production release and 13.59 as a development release containing a security update plus stricter XMP-write error behavior. Genuine distribution bytes are still required before candidate qualification or production approval.

## v12 — Validation follows content, not path (2026-08-22)

Incremental relocation exposed a subtle provenance bug. A corrected Google date can move an unchanged photo from one `YYYY/MM` path to another. The desired destination file does not yet exist when the new immutable metadata revision is prepared, so path-based validation logic could accidentally drop an already qualified validation record.

The rule is now explicit: media-validation evidence belongs to the exact media SHA-256, not to a particular filesystem path. A relocation may carry validation forward only when the existing immutable record is explicitly bound to the same authoritative media SHA-256 and `bytes_unchanged=true`. Legacy/unbound records are not promoted; they remain unqualified and must be revalidated.

## v13 — Repeatable real-JPEG incremental stress qualification (2026-08-22)

A reusable lab tool now qualifies the core incremental path with real decodable JPEGs rather than tiny arbitrary byte strings. It deliberately generates byte-distinct media and then performs initial import, identical re-import, and corrected-date relocation using the exact same media bytes.

The 500-photo run passed:
- 500 new assets on first import;
- 500 unchanged actions on identical re-import with no new revisions;
- 500 relocations when capture dates were corrected into a different month;
- 1,000 immutable revisions after correction (two per asset);
- 500 current qualified media-validation records preserved;
- independent archive audit with zero problems.

This specifically stress-tests the v12 rule that validation evidence follows exact content identity rather than filesystem path. The reported timing values are sandbox qualification observations only, not end-user throughput guarantees.

## v14 — Mixed real-media incremental qualification + stable ffprobe evidence (2026-08-22)

A mixed JPEG/MP4 stress run exposed a real idempotence defect that JPEG-only qualification could not reveal. ffprobe's JSON `format.filename` field reflects the *local path passed to ffprobe*. Because Takeout movies are probed from disposable random staging directories, preserving that field in immutable raw evidence made the same exact video bytes appear metadata-different on each import. This caused false `metadata_update` actions and extra immutable revisions on an otherwise identical re-import.

The archive now strips only this ephemeral probe path from archived ffprobe evidence. Source provenance is not lost: exact Takeout archive/member paths are already recorded separately as occurrence/source evidence. The caller-owned ffprobe object remains unchanged.

A repeatable mixed qualification tool now exercises real decodable JPEGs and real FFmpeg-generated MP4s through initial import, identical re-import, corrected-date relocation, qualified media validation and independent audit. A 100-photo + 20-video run passed with 120 new -> 120 unchanged -> 120 relocated, 240 final immutable revisions, 120 qualified validations and zero audit problems.

## v15 — Mixed-media crash/fault recovery qualification (2026-08-22)

Happy-path incremental stress is insufficient for a personal archive that may process hundreds of gigabytes. v15 therefore adds executable recovery evidence for two realistic interruption boundaries.

First, an initial import may terminate after some assets have fully committed but before the run-level manifest is written. A later run must discover the already committed immutable/current state, classify those assets as unchanged, import the remainder, and finish with one revision per unchanged media object rather than duplicating history.

Second, a corrected-date relocation may be interrupted after the new media/XMP projection exists and the old projection has been removed but before the current metadata pointer advances. The old current pointer remains authoritative after the crash; rerunning the same corrected Takeout must accept the already-correct projection bytes, finish the metadata transition, and avoid duplicate revisions because revisions are content-addressed.

A 20-JPEG + 5-MP4 qualification passed both interruption classes. The final archive contained 25 assets, 50 immutable revisions, 25 qualified validation records and zero independent-audit problems.

## v16 — Source Vault and backup-mirror crash recovery (2026-08-22)

A verified backup cannot be treated as a simple `copytree` operation in a loss-averse archive. An interruption can happen after only part of a destination tree exists, and a retry must distinguish exact already-committed files from foreign or damaged content without overwriting either.

v16 therefore freezes the source tree into `gpa.tree-manifest.v1` evidence and performs a complete preflight before copying any missing file. Every source file must still match the manifest size and SHA-256, and every pre-existing destination must already be the exact expected object. Only missing files are staged, fsynced, committed without overwrite, and rehashed. A later rerun reuses exact completed files. Symlink traversal and unsafe/duplicate manifest paths fail closed.

The raw Takeout Source Vault has an additional interruption boundary: content-addressed ZIP bytes may be durably committed before the occurrence/provenance JSON is written. The qualification hook deliberately crashes at that point. The normal rerun verifies and reuses the already-committed bytes and creates the missing immutable record, so recovery never requires rewriting the original Takeout or inventing provenance.

A disposable 24-source qualification then interrupted and resumed both a 48-file Source Vault mirror and a 120-file finished-archive mirror. The recovered Source Vault and its mirror audited clean, all frozen manifest hashes verified, every original Takeout ZIP retained its original SHA-256, and no GPA staging files remained after the tested commit-boundary interruptions.

This closes the specific v15 blocker for Source-Vault/mirror commit-boundary recovery. It does **not** yet qualify arbitrary power loss during the middle of a low-level file copy, disk-full behavior, hardware I/O errors, removable-drive disconnects, or Windows-specific volume semantics; those remain later fault-injection/release lanes.


### v16 release-gate hardening

Release verification exposed a separate operational risk: external metadata tools must never be able to stall a migration or the qualification gate indefinitely. ExifTool calls now use explicit time bounds and translate timeout expiry into a fail-closed `ExifToolError`; the disposable candidate harness uses a shorter bounded timeout appropriate to its tiny fixture and includes a deliberately hung fake candidate regression.

The automated release gate now also has a machine-readable `gpa.test-suite.v1` runner. It executes each pytest module in a fresh process with an independent timeout and aggregates JUnit results. This is not a way to hide test failures: every module must return success with zero failures/errors. It prevents a bad external subprocess or accumulated interpreter state from making the *test harness itself* unbounded, while still executing the complete suite.


## v16 — Source Vault / mirror crash recovery and external-tool boundedness (2026-08-22)

Source Vault recovery now explicitly handles an interruption after exact Takeout bytes have been durably committed and independently SHA-256 verified but before the occurrence/provenance record is written. Recovery may reuse only the exact content-addressed bytes; mismatches fail closed.

Verified backup mirrors now use a frozen manifest with whole-copy preflight, no-overwrite staged commits, fsync, post-commit SHA-256 verification, exact-match reuse on resume, and rejection of unsafe paths/symlink traversal. A directory mirror still does not prove separate physical-media independence; that remains a retirement-gate fact the user must establish.

A disposable 24-Takeout / 48-file Source Vault mirror / 120-file finished-archive mirror fault qualification converged after deliberate interruptions with zero source-hash changes, zero mirror verification problems, a clean mirrored Source Vault audit, and no leftover GPA staging files.

ExifTool subprocesses are now bounded by fail-closed timeouts. The release test gate runs pytest modules in fresh bounded processes so a hung external helper cannot silently make qualification non-terminating.

## v17 — Backup link isolation and per-record Source Vault binding (2026-08-22)

Hash equality alone is not enough to prove that a redundant copy is physically independent. A filesystem symlink (and, on Windows, a junction/reparse-style link) can make a path inside the apparent backup tree resolve to bytes elsewhere. In the worst case a mirror file could resolve back to the primary archive and still return the correct SHA-256. v17 therefore makes link isolation part of backup correctness: tree-manifest creation, mirror verification and mirror synchronization fail closed on link-like expected paths. A link-backed file can no longer satisfy `redundant_copy_verified` merely because its target bytes match.

The Source Vault audit also needed a stronger distinction between *a byte object that was checked once* and *every provenance record being valid*. Earlier code cached verification by vaulted path and, for duplicate occurrence records, could add the later record's claimed digest without independently proving that the record agreed with the already-verified byte-object binding. v17 caches an exact `(size, SHA-256)` binding instead. Every additional occurrence record must agree with that binding before its digest is admitted to the verified set.

A second crash-recovery edge case was also closed. If a vault already contained valid records, then a later import could crash after a new raw ZIP byte object became durable but before its occurrence record was written. Auditing only the records would leave that new byte object invisible. The audit now inventories the archive-object tree and flags any unreferenced byte object as an orphan. A normal rerun still heals the state by reusing the exact bytes and creating the missing record.

Six automated adversarial regressions cover these cases. The existing 101-object backup fault harness still passes unchanged after the hardening.

Current ExifTool release research was rechecked against upstream history: 13.55 remains identified as the production release and 13.59 as a later development release. Genuine exact candidate bytes are still required before the qualification harness can produce evidence; no production writer promotion is implied by availability or by a future candidate pass.

## v18 — Physical-storage independence evidence hardening (2026-08-22)

A retirement gate must distinguish "second copy" from "independent physical backup." Hash equality proves that a redundant tree matches expected bytes, but two matching directories on one filesystem or physical disk are not independent protection against device loss. A prior test path allowed explicit operator confirmation to override OS evidence that both paths were on the same filesystem/volume. That was unsafe and contradicted the project authority.

`gpa.storage-identity.v2` now makes the evidence model explicit. Same resolved path or same detected filesystem/volume is contradictory evidence and can never be overridden by a confirmation flag. Different detected volume IDs are useful evidence, but are not enough to prove different physical devices because two partitions may live on one disk. When machine evidence is non-contradictory but cannot prove device topology, explicit physical-media confirmation may supplement it.

The assessment is now archival evidence rather than a transient boolean. It records timestamp, platform, method, paths/resolved paths, existing ancestors used for `st_dev` detection, device tokens, same-path/same-volume/separate-volume results, confirmation request/effectiveness, source and explanatory note. `gpa.verification.v2` embeds this evidence separately for the finished archive redundant copy and the raw Source Vault redundant copy.

This is still not a substitute for Windows release qualification. Linux `st_dev` proves filesystem/volume identity only. The Windows lane still needs real NTFS volume/device interrogation and tests on actual separate disks, partitions, junctions and removable/NAS targets before the final retirement gate can claim strong automatic physical-device proof.

## v19 — Parallel-authority reconciliation and bounded complete-suite hardening (2026-08-22)

Two independently preserved v16 descendants existed at the same time. One branch advanced backup/source-vault link isolation, per-record Source Vault binding, orphan-byte detection, and later physical-storage independence evidence. A separately reconciled v16 branch added bounded ExifTool subprocesses, a more comprehensive Source Vault + finished-archive mirror fault qualification, `MirrorBuildReport`/`materialize_verified_mirror`, and a machine-readable bounded complete-suite release gate. Neither branch alone contained every improvement.

v19 was built by a true three-way merge using the earlier 348-test v16 checkpoint as the common ancestor. The reconciled 349-test v16 checkpoint was independently recovered at SHA-256 `8d974bf4ca03dc94646cf5868bd0d5e4cf2ddc6c37049ae02cfd1ae8f07e05d1`; the later branch carried the v17/v18 safety hardening. The merge deliberately retains both mirror APIs for compatibility: `materialize_verified_mirror()` provides detailed copied/reused/verified counts, while `synchronize_mirror()` returns the final verification report. Both use the same full-preflight, staged, fsynced, no-overwrite, link/junction-rejecting implementation.

The Source Vault fault hook is standardized at the precise durability boundary: it fires only after a new content-addressed byte object has been committed and verified, before the occurrence record is written. A rerun that sees the already-existing exact bytes does not fire the hook again and can therefore heal the missing provenance record. Source Vault audit still rejects link/junction traversal, binds every occurrence record to exact `(size, SHA-256)` evidence, and inventories orphan byte objects.

The bounded release runner was also strengthened. It now includes `tests/test_backup_recovery.py`, disables third-party pytest plugin autoload inside each isolated subprocess, and supports bounded parallel module execution. Parallelism does not share pytest interpreter state: each module is still a separate Python process with its own timeout and JUnit XML. The v19 pre-check produced `gpa.test-suite.v1` evidence for **363 tests, 0 failures, 0 errors, 0 skipped**, across eight isolated modules.

The merged `gpa.stress-backup-recovery.v1` qualification used 20 raw Takeout ZIPs, a 40-file Source Vault mirror, and a 100-file finished-archive mirror. Deliberate Source Vault byte/provenance interruption, Source Vault mirror interruption, and finished-archive mirror interruption all healed on rerun; original Takeout hashes were unchanged, mirrored Source Vault audit passed, mirrors verified, and no GPA staging files remained.

A later persistent-library check found that a separate v18 reconciliation checkpoint had already been promoted while this merge was being prepared. That checkpoint was recovered independently at SHA-256 `6e0104dc0655e7a91128fe7f4f30008950595fbf5d6e26777f1d252578a83329` and reproduced at **355/355**. Direct source comparison showed that its ExifTool execution/qualification implementation is already present unchanged in v19. The remaining differences are intentional extensions in v19: detailed mirror materialization/reporting, broader Source Vault + finished-archive fault qualification, storage-identity/retirement evidence v2, and bounded parallel release-gate execution. The Source Vault fault hook also fires only for a newly committed byte object so the recovery rerun cannot repeatedly trigger the same synthetic crash on already-verified bytes.

This reconciliation means future work must never choose between the old v16/349, v17, or persisted v18/355 lines. v19 is their combined descendant and is the only valid forward base once its clean-room checkpoint gate completes.

## v20 — Windows physical-device topology for redundant-storage evidence (2026-08-23)

Different drive letters, mount points, or filesystem device identifiers are not enough to prove independent hardware. Windows can place multiple volumes/partitions on one physical disk, and a dynamic/spanned volume can depend on more than one physical disk. The retirement gate therefore needs the underlying disk topology rather than a drive-letter comparison.

Microsoft documents the relevant local-volume chain as resolving the containing volume, resolving the volume GUID, opening the volume, and issuing `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`. The returned `VOLUME_DISK_EXTENTS` structure contains one or more `DISK_EXTENT` records with the physical `DiskNumber`, starting offset and extent length. The implementation uses the complete disk-number set so a spanned volume cannot be mistaken for an independent one simply because one extent differs.

A subtle path rule matters: the topology probe is handed an absolute existing ancestor path. Microsoft documents volume-path resolution behavior for relative paths that can otherwise fall back to a default/boot volume, which would be unacceptable for archive-retirement evidence.

`gpa.storage-identity.v3` now combines the previous filesystem/volume evidence with this Windows physical topology. If both paths yield supported local topology, overlapping disk sets are conclusive same-device evidence and block physical-media confirmation. Disjoint disk sets are positive machine proof and can satisfy the physical-independence fact without asking the operator to restate it. If topology is unsupported, fails, or is remote/network, the result stays unproven. Existing manual confirmation can supplement ambiguity only in the absence of contradictory machine evidence.

SMB/NAS is intentionally not forced through the local-volume path. Microsoft documents the volume-management disk-extents lane as unsupported for SMB, so remote paths are detected before volume GUID/IOCTL calls and fail closed for automatic proof. A future NAS lane will need evidence tied to the remote storage system/device itself rather than pretending a client-side drive mapping identifies hardware.

The ctypes adapter requests zero data access when opening a volume and closes every handle. `ERROR_MORE_DATA` is handled with a bounded retry and the variable-length extent buffer is parsed using native ctypes ABI alignment. The release tests use an injectable API to exercise policy deterministically on non-Windows CI, including same-disk partitions, disjoint disks, overlapping spanned volumes, remote volumes, parser truncation and CLI fail-closed behavior. This is not a substitute for running the adapter on actual Windows 64-bit hardware before release.

The v20 pre-release suite expanded from 363 to 375 tests. The unchanged backup fault harness also passed again, confirming that this storage-evidence work did not regress Source Vault/mirror recovery or alter raw Takeout bytes.

## v21 — Distinguishing Windows disk numbers from physical hardware (2026-08-23)

The v20 disk-extents lane solved the same-disk-partition problem, but a deeper Windows storage review identified an important remaining abstraction boundary. `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS` returns disk numbers backing a volume, yet Windows disk objects are not necessarily independent pieces of physical media. Microsoft exposes storage bus types including Virtual, File-Backed Virtual, Storage Spaces, iSCSI, RAID and fabric-attached classes. Two VHD-backed disk objects can therefore have different disk numbers while ultimately sharing one host device or failure domain.

The Windows `STORAGE_DEVICE_DESCRIPTOR`, retrieved through `IOCTL_STORAGE_QUERY_PROPERTY` with `StorageDeviceProperty`, provides both the storage bus type and an optional device serial. v21 uses that descriptor as a second evidence layer after volume extents. Disk numbers remain useful topology facts, but automatic physical-media proof now requires eligible direct/local bus classes and usable distinct serial identities for every disk involved.

The direct/local automatic allow-list is intentionally conservative: ATAPI, ATA, IEEE-1394, USB, SAS, SATA, SD, MMC, NVMe, Storage Class Memory and UFS. Generic SCSI, Fibre Channel, RAID, iSCSI, Virtual, File-Backed Virtual, Storage Spaces, NVMe-over-Fabrics, unknown and future/unrecognized types do not automatically prove physical independence. Blocking these classes does not assert that they are unsafe; it means the client-side descriptor does not by itself establish the project's required physical failure-domain fact.

A serial number is also required for automatic proof. Missing serials keep the result unproven. Duplicate serials within a multi-disk volume cause that volume's hardware identity to be ineligible. A serial overlap between the primary and backup disk sets is stronger: it is treated as conflicting machine identity and blocks manual override until resolved, even when disk numbers differ. Comparisons are case-insensitive while the archival evidence retains the reported strings.

This produces three deliberately separate facts in `gpa.storage-identity.v4`: filesystem/volume separation, disk-number separation, and physical-device proof. `gpa.verification.v4` carries both disk-number and physical-device facts for the finished archive and Source Vault redundancy. A user can therefore see that Windows found different disk objects without the system falsely translating that into different physical hardware.

The production ctypes adapter opens `\\.\PhysicalDriveN` with zero data access, submits a bounded `STORAGE_PROPERTY_QUERY`, retrieves a bounded descriptor size, and parses the variable descriptor offsets defensively. Unsupported, malformed, truncated or oversized data fails closed. Automated tests use injectable device APIs and synthetic descriptor buffers; real Windows ABI/device-stack qualification is still required.

This v21 correction was made before any real Windows qualification was accepted, so no stored real-hardware retirement evidence needs migration. v20 remains a valid historical development checkpoint, while v21 is the safer forward policy once its clean-room gate is promoted.



## v22 — Exact ExifTool distribution admission before qualification (2026-08-23)

The earlier ExifTool qualification harness correctly required a genuine candidate executable, actual metadata mutation, unchanged JPEG payload, decodability and exact readback. What remained missing was an auditable path from an upstream Windows distribution archive to the executable presented to that harness. Without that boundary, a user could accidentally qualify an unverified, repackaged or wrong-version executable.

The upstream state was rechecked before implementation. ExifTool's version history still identifies **13.55 as the most recent production release** and classifies later releases such as **13.59 as development releases**. Fossies' preserved original Windows x64 archives record:
- `exiftool-13.55_64.zip`: 11,117,157 bytes, SHA-256 `9fadaf5221dcb07a5d018e21c8529e257917d2fad1fdfc8e64855c4fb73293df`; archive listing includes `exiftool-13.55_64/exiftool(-k).exe` and `exiftool-13.55_64/exiftool_files/...`.
- `exiftool-13.59_64.zip`: 11,183,675 bytes, SHA-256 `44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec`; the same expected Windows distribution shape is present.

ExifTool's own Windows installation page instructs users to extract the `exiftool-13.59_xx` folder and rename `exiftool(-k).exe` to `exiftool.exe` for command-line use, while keeping `exiftool_files` with it. v22 implements that rename as a namespace operation and verifies the executable SHA-256 before and after, so preparation cannot silently alter executable bytes.

The new `gpa.exiftool-distribution-admission.v1` layer pins candidate ID, version, platform, architecture, expected filename, upstream download URL, exact byte size, SHA-256, top-level directory and expected executable names. A cache hit is accepted only if exact size and hash match. A network acquisition writes only to a private staging file; the byte count cannot exceed the pinned size, the final count must equal it, and SHA-256 must match before no-overwrite cache commit. Conflicting existing cache content blocks the operation instead of being replaced.

ZIP extraction is also treated as hostile-input processing even though the production candidates are hash-pinned. Before any member write, the code rejects traversal, absolute/backslash paths, duplicate Windows/Unicode-normalized names, symlinks, encrypted members, non-regular special files, Windows reserved device names, alternate-stream colons, invalid filename characters, trailing-dot/space ambiguity, overlong components, excessive member counts and excessive uncompressed size. Extraction is member-by-member into a private staging directory, not `extractall`, and an incomplete package is removed rather than committed.

The resulting command-line workflow intentionally separates two decisions:
1. `gpa-lab admit-exiftool`: prove that the ZIP is the exact pinned distribution and safely prepare it;
2. `gpa-lab qualify-exiftool`: fingerprint the prepared executable/distribution and run the existing genuine mutation/payload/decode/readback qualification.

This separation is important for governance. **Admission is provenance, not approval.** Even a perfectly admitted official archive must not enter the production embedded-write allow-list unless its exact executable/distribution subsequently passes the mutation harness and a production promotion decision records the exact identity and qualification evidence.

The current sandbox still cannot fetch the Windows ZIP through its restricted transport and cannot execute Windows binaries, so no genuine 13.55/13.59 pass is claimed here. v22 instead makes that future Windows run deterministic and auditable.

Research sources rechecked for this tranche:
- ExifTool Version History, `https://exiftool.org/history.html` — 13.55 production status; 13.59 development/security update.
- Installing ExifTool, `https://exiftool.org/install.html` — official Windows extraction and `exiftool(-k).exe` rename procedure.
- Fossies preserved `exiftool-13.55_64.zip` and `exiftool-13.59_64.zip` archive listings — exact sizes, SHA-256 values and member layout; both identify `https://exiftool.org/` as original source.


## v23 — Binding ExifTool admission, qualification and live artifacts (2026-08-23)

v22 created a safe boundary between an upstream ExifTool ZIP and the executable passed to the mutation qualification harness. A further provenance review found that two correct reports could still be accidentally mixed, or a prepared distribution could change after its admission report was written. Report-file hashes alone would identify which JSON documents were bundled but would not independently prove that the artifacts still matched those documents.

v23 resolves this by giving admission and qualification one shared distribution-tree fingerprint. The tree digest is calculated over every regular file as `(relative path, size, SHA-256)` in deterministic order. Symlink/junction-like and non-regular objects are refused. Admission records this digest after extraction and the documented `exiftool(-k).exe` → `exiftool.exe` rename. Qualification computes the same digest before running the candidate, so changes anywhere in `exiftool_files` are visible rather than only changes to the top-level executable.

The new `gpa.exiftool-evidence-bundle.v1` combines the pinned archive identity, executable SHA-256, complete prepared-distribution SHA-256, qualification ID/harness, and exact SHA-256 values of both input reports. Crucially, the bundle cannot become review-ready merely because JSON fields agree. Bundle creation re-opens the live pinned ZIP and verifies its exact size/SHA-256 again, verifies that the supplied executable is inside the supplied distribution root, re-hashes the executable, and re-fingerprints the full distribution tree. These live values must match both reports.

This addresses accidental evidence substitution and post-report change. A qualification report for one exact prepared tree cannot be paired with another tree just because both claim the same ExifTool version. Likewise, a manually edited report cannot by itself make a different ZIP/executable/tree pass the live checks. This is an evidence-integrity control, not a hostile-local-admin security boundary.

The semantic boundary is explicit: `ready_for_promotion_review` is not equivalent to production approval. The evidence bundle always emits `production_write_approved=false`. A later production decision must still consider format-specific write qualification, exact allow-list policy and all archive-safety requirements.


## v24 — Per-format and composite-relationship write qualification (2026-08-23)

ExifTool's supported-file-type documentation marks many archive-relevant formats as writable, including JPEG, PNG, HEIC/HEIF, AVIF, MOV, MP4 and numerous RAW families. That upstream capability fact must not be interpreted as a GPA archive-safety result. The project requires a stronger question: for this exact ExifTool executable/distribution and this exact media family, did a real metadata mutation preserve the encoded media payload or other required relationship invariants, remain decodable, and read back exactly?

v24 therefore adds a separate format-write qualification policy. `gpa.format-write-matrix.v1` records the media family, payload-fingerprint method, validator contract, round-trip harness and current qualification readiness. Only standalone JPEG is currently marked qualification-ready because the existing `gpa.exiftool-jpeg-roundtrip.v1` harness already performs real mutation, proves the whole file changed, proves the JPEG image payload did not change, verifies decode, and reads the written metadata back.

PNG and QuickTime/ISO-BMFF already have conservative structural fingerprints in GPA (`png-visual-payload-v1` and `iso-bmff-mdat-v1`) but remain unqualified for production writing until genuine format-specific ExifTool round-trip harnesses are completed. HEIF/AVIF and RAW remain more strongly blocked because the project does not yet have an adequate encoded-image/RAW payload preservation contract. A decoder-only or pixel-only equality test is not accepted as a substitute for proving no unintended recompression.

Composite media introduces another boundary. A JPEG that is a Motion Photo is not equivalent to a standalone JPEG because metadata edits can change appended-video offsets or descriptor structure. Likewise, a MOV participating in a Live Photo must preserve the content-identifier relationship with its still component. v24 therefore requires an explicit relationship classification at the production write API and defines separate, currently unqualified `motion-photo-composite-v1` and `live-photo-pair-v1` relationship profiles. There is deliberately no default relationship classification.

The low-level ExifTool adapter remains useful for qualification work, but `ProductionExifToolAdapter` now sits behind an additional exact authorization check. Even after a future exact ExifTool build is added to the build allow-list, production `.write()` remains blocked unless the prepared-distribution digest, media format profile and relationship scope also have an exact production approval. Both production approval sets remain empty in v24.

Research source rechecked for this tranche: ExifTool's official command documentation file-type table, which reports read/write capability for formats including JPEG, PNG, HEIC/HEIF, AVIF, MOV, MP4 and many RAW formats. GPA treats those labels only as upstream capability information, never as payload-safety evidence.


## v25 — PNG and suffix-scoped QuickTime round-trip qualification infrastructure (2026-08-23)

v24 made production writes per-format and relationship-aware, but only JPEG had a concrete mutation/payload/decode/readback harness. v25 adds a separate `gpa.exiftool-format-qualification.v1` report so future Windows evidence can prove format behavior without changing the legacy JPEG build-qualification schema or weakening v23 evidence compatibility.

For PNG, the harness creates a real non-flat RGBA fixture, records the exact ExifTool executable/distribution identity, performs a metadata write, requires the whole file to change, requires `png-visual-payload-v1` to remain unchanged, validates the result through Pillow, records the exact validator version/build fingerprint, and requires exact XMP date/description readback. A fake candidate that merely prints success, changes pixels/encoded image chunks, returns wrong metadata or hangs fails closed.

For QuickTime/ISO-BMFF, the harness creates a real video fixture with an exact fingerprinted FFmpeg generator, validates it through an exact fingerprinted ffprobe build, performs the candidate metadata write, requires the whole file to change while every `mdat` media payload byte remains unchanged, revalidates the output, and requires exact metadata readback. The test lane explicitly detects `mdat` modification.

A review caught an over-broad initial design before freeze: one MP4 fixture must not qualify `.mov`, `.mp4` and `.m4v` as a single approval scope. v25 therefore advances the format-matrix schema to `gpa.format-write-matrix.v2` and splits those suffixes into `mov-single-v1`, `mp4-single-v1` and `m4v-single-v1`. The harness supports all three, and regression tests prove an approval for one suffix does not authorize either of the others.

The new command `gpa-lab qualify-exiftool-format` produces machine-readable evidence for `png-single-v1`, `mov-single-v1`, `mp4-single-v1` or `m4v-single-v1`. The format profiles remain production-blocked until a genuine exact candidate passes and a separate promotion decision is made. Harness existence is not approval, and both production approval sets remain empty.


## v26 — Per-format evidence chain and promotion traceability (2026-08-23)

A format-specific metadata-writer pass is not sufficient evidence on its own. It must be demonstrably tied to the same exact ExifTool distribution whose build-level admission/qualification evidence was reviewed. v26 therefore introduces a second evidence layer that binds one format report to one exact build evidence bundle and then re-verifies the live pinned archive, executable and complete prepared distribution tree at bundle time.

The bundle also verifies the registered harness for the exact format profile, validator implementation/version/build fingerprint, unchanged encoded media payload, successful independent decode/parse and exact metadata readback. Report-file SHA-256 values are preserved so the evidence identifies the exact serialized inputs used for review.

Internal IDs are treated as evidence, not trusted labels. v26 recomputes the canonical base ExifTool evidence bundle ID and the canonical per-format qualification ID from their immutable identity fields. A copied or tampered ID therefore blocks format-promotion readiness even when the surrounding JSON file is itself hashed.

A future production format approval must include the exact 24-character reviewed format-evidence bundle ID. This creates an auditable chain from production policy back through format qualification, exact build evidence, exact candidate archive and live prepared distribution. Creating a review-ready bundle still never enables writes automatically; both production allow-lists remain explicit code/governance decisions and are currently empty.


## v27 — HEIF/HEIC/AVIF protected-item payload qualification (2026-08-23)

HEIF/AVIF cannot safely use the QuickTime shortcut of hashing a whole `mdat`. Still-image data is represented as HEIF items whose byte extents are described by `iloc`; AVIF explicitly permits coded payload in either `mdat` or `idat`, and a primary image may be a derived image referencing multiple coded items. EXIF and XMP are separate metadata items rather than part of the coded image payload.

The v27 fingerprint therefore parses the item catalogue (`iinf`/`infe`) and item-location table (`iloc`) and hashes all item payloads except exact recognized EXIF items and XMP MIME items (`application/rdf+xml`). Every unknown MIME/item type is treated as protected data. The fingerprint is independent of item-ID numbering and metadata extent placement, while preserving item type, extent order and exact protected bytes. Only local `mdat` construction method 0 and local `idat` construction method 1 are admitted. External data references, construction method 2, duplicate/unknown item IDs, out-of-range extents, ambiguous box counts and metadata/protected extent overlap fail closed.

Approval scope is also separated. AVIF (`.avif`), HEIC (`.heic`) and generic HEIF (`.heif`) no longer share one production profile. AVIF has a real round-trip harness because this qualification environment can create a genuine AV1 Image Item and libheif can independently decode it. HEIC remains blocked because this environment lacks a genuine HEVC HEIF encoder; manufacturing a synthetic byte sequence would not be acceptable evidence for a production metadata writer. Generic `.heif` stays blocked because the extension does not establish a codec.

The media validator was strengthened during review. `heif-info` proves useful structure information but the safety contract requires real decodability. HEIF/AVIF validation now uses `heif-convert` to decode every produced image to temporary PNG output and Pillow independently verifies those decoded files. The exact heif-convert executable SHA-256 and the exact Pillow code/native-core fingerprint are combined into validator provenance. The original media remains SHA-checked before and after the supposedly read-only validation.

Research sources rechecked for this tranche:
- AV1 Image File Format v1.2.0, https://aomediacodec.github.io/av1-avif/v1.2.0.html — AVIF is HEIF-conformant; AV1 image items; primary derived images; `iinf`/`iloc`; coded payload may be in `idat` instead of `mdat`.
- libheif README, https://github.com/strukturag/libheif/blob/master/README.md — primary-image API, multiple/auxiliary/thumbnail images, EXIF/XMP metadata blocks and HEIC/AVIF decoding.
- libavif read implementation notes, https://github.com/AOMediaCodec/libavif/blob/main/src/read.c — HEIF items include image planes and EXIF/XMP metadata and are located/typed through `iloc`/`iinf`.


## v28 — Genuine HEIC fixture admission and round-trip qualification (2026-08-23)

v27 deliberately left HEIC blocked because the qualification environment could decode HEVC-in-HEIF but could not create a genuine HEIC image. Simulating HEVC payload bytes would have made the qualification result meaningless. v28 therefore treats the HEIC test image itself as supply-chain evidence.

A small upstream libheif test asset, `tests/data/rainbow-451x461.heic`, is pinned to libheif commit `1a3583bcce77de6d3f8701c0758e3954863681ba`. The exact upstream object is 7080 bytes, Git blob SHA-1 `6691f50f39bd69871a2abe284de2ef9f5243bc66`, and SHA-256 `4b2ce727f093944975f143ba2b39c4c64511b766d94552f8d51a755916e7f983`. Its file structure identifies an HEVC (`hvc1`) image item plus XMP metadata. The source bytes are included only under `tests/fixtures/heic/` with a machine-readable provenance manifest and LGPL-3.0 license text; they are never archive media or a production dependency.

Fixture admission is fail-closed. GPA requires a regular non-symlink file, exact byte count, exact SHA-256 and exact Git blob identity (`SHA1("blob <length>\0" + bytes)`). The Git SHA-1 is used solely to prove equality to the named upstream Git object; SHA-256 remains the cryptographic content-admission hash. The admission report records repository, pinned commit, source path/URL, license basis and a permanent `production_write_approved=false`.

`gpa.exiftool-heic-roundtrip.v1` copies only an admitted fixture into a qualification workspace and re-admits the copy before testing. It then applies the v27 `heif-item-payload-v1` fingerprint, validates/decodes through the exact heif-convert + Pillow validator chain, performs the candidate metadata mutation, requires the whole file to change while all protected non-metadata HEIF item payloads remain byte-identical, decodes again, and requires exact metadata readback. The original fixture itself is checked to remain unchanged.

The v26 format-evidence chain is also extended for profiles that require an external fixture. A HEIC evidence bundle cannot become review-ready unless its qualification report contains the exact pinned fixture ID, profile, SHA-256, source commit, Git blob ID and license, with successful exact-byte and Git-object verification and no production-approval flag. These fixture fields are included in the canonical format-evidence bundle ID.

This closes the HEIC harness gap, but it does not change production policy. The exact Windows ExifTool 13.55 candidate still has to pass this harness on Windows and be separately reviewed/promoted. Generic `.heif` stays blocked because that suffix alone does not prove the codec.

Fixture provenance sources reviewed for this tranche:
- `strukturag/libheif` upstream test data at pinned commit `1a3583bcce77de6d3f8701c0758e3954863681ba`; Git object identity is recorded and independently recomputed.
- libheif test code explicitly gates HEIF HEVC decoding on `heif_compression_HEVC`, confirming the codec-specific test lane.
- libheif `COPYING` states the library licensing boundary; the codec-corpus HEIC conformance documentation independently classifies libheif testdata as LGPL-3.0.


## v29 — Motion Photo relationship round-trip qualification and evidence binding (2026-08-23)

The Android Motion Photo 1.0 specification was rechecked before implementation. A Motion Photo is one primary JPEG/HEIC/AVIF image followed by an appended video, with Camera XMP controlling presentation and Container XMP defining the media-item directory. `Camera:MotionPhoto=1` is explicitly not definitive because editors can preserve XMP after stripping the video. Readers must confirm the video exists. The old MicroVideo fields, including `MicroVideoOffset`, are deleted by Motion Photo 1.0 and must be ignored; the video location is instead derived from the secondary item's `GContainer:ItemLength`.

The directory is ordered and tightly packed. The Primary item must be first; secondary media require a positive Length; the MotionPhoto item must be unique and terminal, with no bytes after it. JPEG primary Padding is optional, while HEIC/AVIF Motion Photos require an 8-byte primary Padding corresponding to the terminal `mpvd` box header. The appended video must contain at least one primary AVC, HEVC or AV1 video track. These details mean a metadata writer must be qualified against the composite relationship, not merely against the still-image suffix.

The new JPEG relationship harness therefore generates a real H.264/AVC MP4, embeds a Motion Photo 1.0 Camera/Container XMP directory in a real JPEG, and appends the exact MP4 bytes. It deliberately does not include legacy MicroVideo fields. After the candidate metadata write, qualification requires all of the following at once: exact Motion Photo structural rediscovery without legacy fallback; a byte-stable JPEG visual payload fingerprint; byte-identical appended MP4; byte-stable `mdat`; unchanged declared video length; video remains the final resource; independent JPEG and video decode/parse validation with exact validator build fingerprints; whole-file mutation; and exact metadata readback.

Relationship evidence is now separately chained. `gpa.exiftool-relationship-evidence-bundle.v1` accepts only an already review-ready format evidence bundle plus a passing relationship qualification for the same exact format profile, candidate version, executable SHA-256 and prepared-distribution SHA-256. It recomputes both evidence IDs, binds the exact report-file SHA-256 values, and re-hashes the live executable and distribution. Production policy schema v3 requires a separate reviewed relationship-evidence bundle ID whenever a composite relationship profile is approved.

This does not make Motion Photo writes production-ready. The current harness covers JPEG Motion Photo 1.0 only; HEIC and AVIF Motion Photo relationship qualification still require codec-specific composite fixtures, and a genuine exact Windows ExifTool candidate must pass before any review/promotion. Live Photos also remain a separate relationship lane.

Primary research source rechecked for this tranche:
- Android Developers, `Motion Photo format 1.0`, https://developer.android.com/media/platform/motion-photo-format — Motion Photo flag semantics, deletion of legacy MicroVideo fields, ordered/tightly-packed Container directory, Item Length/Padding rules, terminal MotionPhoto item, `mpvd` behavior for HEIC/AVIF, and required primary video track codecs.


## v30 — Closing the standalone JPEG format-evidence chain (2026-08-23)

A forward-policy audit found a mismatch introduced by the gradual hardening of ExifTool governance. The original JPEG candidate harness (`gpa.exiftool-jpeg-roundtrip.v1`) predates the per-format evidence schema. Later policy correctly required every production format approval to cite a reviewed 24-character format-evidence bundle ID, but the generic `qualify-exiftool-format` command only supported PNG, QuickTime, AVIF and HEIC. Standalone JPEG was therefore the only profile marked qualification-ready without a direct producer for `gpa.exiftool-format-qualification.v1`.

That was a fail-closed gap rather than an unsafe write path: production approvals are empty, so JPEG writes could not accidentally become enabled. But it would have blocked a legitimate future Windows promotion because a legacy build qualification report cannot simply be relabeled as a per-format report. v30 closes the gap by adding a real JPEG path to the per-format harness while importing the canonical JPEG harness ID from the legacy build-qualification module so the two layers cannot silently drift in naming.

The new per-format JPEG run is intentionally narrower than the base-build qualification and complementary to it. The base build gate still proves the richer EXIF/GPS/XMP mutation/readback contract. The format-specific gate proves the named `jpeg-single-v1` payload/decoder contract using a real non-flat JPEG, exact executable/distribution fingerprints, byte-stable JPEG visual payload, fingerprinted Pillow validation, whole-file mutation and exact XMP date/description readback. The v26 format-evidence bundler then requires both that format report and an already review-ready base evidence bundle for the same exact candidate artifacts.

This preserves the governance separation: admission proves upstream bytes, base qualification proves the exact ExifTool build can safely perform the core JPEG mutation contract, format qualification proves the exact standalone-JPEG media-family contract, format evidence binds both to live artifacts, and only a later explicit production decision could add the exact reviewed bundle ID to policy. No such production decision is made in v30.

## v31 — Independent Apple Live Photo pair-integrity evidence (2026-08-23)

Apple's AVFoundation documentation explicitly states that a Live Photo movie always contains `AVMetadataQuickTimeMetadataKeyContentIdentifier`, and that this value associates the movie with a similar identifier in the EXIF MakerNote of the corresponding still image. ExifTool's Apple tag table independently identifies Apple MakerNote tag `0x0011` as `ContentIdentifier` and documents the Apple iOS MakerNote as a writable/self-contained EXIF structure. Community reverse-engineering also identifies the separate timed `com.apple.quicktime.still-image-time` movie metadata track as part of full Live Photo behavior.

The archival conclusion is deliberately split into two facts. Matching independently parsed content identifiers can prove pair identity, but it is not sufficient to claim end-to-end Photos.app usability. v31 therefore adds `gpa.live-photo-pair-integrity.v1` for JPEG + MOV pairs. The still identifier is parsed directly from the JPEG EXIF/TIFF structure and Apple MakerNote IFD rather than trusting ExifTool; the movie identifier is read through ffprobe with the exact ffprobe binary SHA-256 recorded. Both components are independently media-validated and hashed before/after inspection. UUID normalization prevents superficial case differences while malformed identifiers fail closed.

The report exposes `identity_verified`, `component_health_verified` and `photos_usability_verified` as separate facts. The first two may become true when IDs match and both media components validate without mutation. `photos_usability_verified` remains false because this lane does not yet independently validate the movie's still-image-time/timing-resource semantics or import the pair into Apple Photos. This avoids converting a strong identity observation into a stronger compatibility claim than the evidence supports.

This lane is read-only and does not change the ExifTool production approval policy. A future Live Photo metadata-writer relationship qualification must additionally prove pair preservation across writes, timing/resource semantics and appropriate genuine fixture/platform evidence.

Research sources rechecked for this tranche:
- Apple Developer Documentation, `AVCapturePhotoSettings.livePhotoMovieMetadata` — Live Photo movie content identifier and association with the corresponding still-image EXIF MakerNote.
- Apple Developer Documentation, `AVMetadataIdentifierQuickTimeMetadataContentIdentifier` — QuickTime content-identifier metadata identifier.
- ExifTool `Image::ExifTool::Apple` tag table / Apple TagNames — Apple MakerNote tag `0x0011` ContentIdentifier and Apple MakerNote structure.
- LimitPoint LivePhoto reference implementation — documents the commonly used timed `com.apple.quicktime.still-image-time` resource relationship; treated as implementation evidence, not as Apple normative specification.


## v32 — Independent Live Photo still-time semantics and HEIC primary-EXIF identity (2026-08-23)

The v31 split between pair identity and full Live Photo usability exposed the next evidence boundary: the movie's `com.apple.quicktime.still-image-time` marker is a *timed metadata track*, not a normal static tag whose stored marker value is the still timestamp. Apple documents `photoTime` as the time in the video corresponding to the still photo, and real Live Photo implementations create a QuickTime `mdta/com.apple.quicktime.still-image-time` metadata sample. Recent Apache Tika TIKA-4777 work independently confirmed the important binary detail: the metadata sample value is a constant marker, while the actual still moment is the presentation start of the single-sample `mebx` track. In common iPhone files a leading empty edit in `elst` delays that one-tick sample; the edit duration is expressed in the movie timescale.

v32 therefore adds a fully independent moov-only parser. It walks bounded ISO-BMFF boxes, requires exactly one `moov`/`mvhd`, recognizes only `meta` handler tracks with a `mebx` sample entry that explicitly declares the Apple key through the QuickTime `keys`/numeric-local-key/`keyd` structure, and requires exactly one `stts` sample. Version-0 and version-1 edit lists are supported. A leading empty edit produces `duration * 1,000,000 / movie_timescale`; no leading empty edit produces a meaningful zero. A non-leading empty edit, duplicate still-image-time track, multi-sample track, malformed box length, malformed key declaration or ambiguous structure fails closed. The parser never reads `mdat` and therefore cannot confuse the constant metadata payload with the actual time.

The implementation was cross-checked against Apache Tika's public `testMP4_QuickTimeMetadata.mov` fixture used for TIKA-4777. GitHub identifies that object as blob `87fa3f7ac10aba215da296cc41be28786b92b775`; the fetched bytes hash to SHA-256 `cf02fa537c017934a7e5d1522908a79dc2fa62bceb96dc471657598607c634a4`. GPA independently returned 1,233,333 microseconds from its first track's 740-unit leading empty edit and 600-unit movie timescale, matching the published TIKA-4777 interpretation. This is an algorithm cross-check, not an Apple Photos compatibility certification.

Modern Apple Live Photos also commonly use HEIC stills, so JPEG-only MakerNote parsing was not enough. HEIF stores Exif as an item rather than a JPEG APP1 segment. ISO/IEC 23008-12 defines an ExifDataBlock with a 32-bit TIFF-header offset, and HEIF metadata items describe image items using `cdsc` item references. v32 resolves the file-level primary image through `pitm`, requires a single Exif item whose `cdsc` reference targets that primary image, resolves its `iloc` extents without external data references, applies the ExifDataBlock TIFF offset, and then reuses the independent Apple MakerNote parser. This prevents an auxiliary image's Exif from being silently treated as the Live Photo still identity.

The HEIC regression does not manufacture an HEVC bitstream. It starts from GPA's already pinned upstream libheif HEVC fixture, changes only its existing metadata item into a standards-shaped Exif item carrying a synthetic Apple ContentIdentifier, and proves the protected HEVC item fingerprint is identical before/after. `heif-convert` plus Pillow continue to decode the result. This gives real HEIC container/codec coverage for the metadata-selection logic while still avoiding any claim that the synthetic MakerNote is a genuine iPhone capture.

The Live Photo evidence schema advances to `gpa.live-photo-pair-integrity.v2` and now reports timing-resource evidence separately from identity and component health. Even when all three are true, `photos_usability_verified` remains false until a genuine Apple-platform import/playback acceptance lane exists. Read-only integrity evidence does not authorize metadata rewriting, and the production ExifTool approval sets remain empty.

Research sources rechecked for this tranche:
- Apple Developer Documentation, `AVCapturePhotoSettings.livePhotoMovieMetadata` and `PHLivePhotoEditingContext.photoTime` — paired resource identifier and still-photo time semantics.
- Apple LivePhotosKit JS `photoTime` / `metadataVideoSrc` — original iPhone MOV can supply the still-photo time required for playback.
- Apache Tika TIKA-4777 / PR #2936 — single-sample `mebx` key declaration, leading-empty-edit timing, zero-vs-absent semantics and real iPhone timing example.
- ISO/IEC 23008-12 Annex A / HEIF metadata clauses — ExifDataBlock TIFF-header offset and `cdsc` association of metadata items to image items.

## v34 — HEIC/AVIF Motion Photo 1.0 relationship qualification (2026-08-24)

Android's current Motion Photo 1.0 specification requires ISOBMFF-based primaries such as HEIC and AVIF to terminate the image portion with a top-level `mpvd` box containing all video bytes. The primary directory item must declare `Padding=8`, accounting for the normal `mpvd` size+type header, while the MotionPhoto item's `Length` is the actual video payload length. The MotionPhoto resource must be terminal with no bytes after it. Legacy MicroVideo fields are deleted by the 1.0 specification and are not accepted as the qualification path.

v34 converts those rules into executable evidence. GPA now reads HEIF/AVIF XMP directly from file-level `meta` item catalog/location structures and does not trust ExifTool to tell the harness whether the relationship survived. The pinned HEIC qualification fixture is admitted by exact SHA-256 and Git blob identity, then only its existing XMP metadata payload is replaced at the same extent size. A protected HEIF item fingerprint proves the HEVC image payload remains unchanged before the composite is even constructed. AVIF uses a genuine Pillow AVIF file with a real XMP item.

Both ISOBMFF variants receive a real FFmpeg/libx264 MP4 video inside an explicit-size terminal `mpvd` box. After a candidate metadata write, qualification independently re-parses the relationship, validates `Padding=8`, exact terminal video length, explicit `mpvd` header, protected image-item fingerprint, exact video bytes and `mdat`, and independently decodes both separated components. Duplicate structural XMP declarations are treated as ambiguity and fail closed.

Passing this disposable harness is still evidence only. A production write requires the exact Windows ExifTool build/distribution, per-format evidence, relationship evidence and explicit promotion; those allow-lists remain empty.

## v35 — Camera RAW decoder-health evidence without RAW rewriting (2026-08-24)

Camera RAW needs two separate safety questions. First, can the preserved source actually be opened and decoded by an identified decoder build? Second, can metadata be rewritten without damaging the format-specific image payload or camera-private structures? A positive answer to the first does not imply the second. v35 therefore adds only the read-only decoder-health lane and keeps RAW writer qualification blocked.

LibRaw remains an appropriate decoder foundation because it supports a broad set of camera RAW families and current 0.22-series releases include newer DNG capabilities. rawpy provides a Python binding over LibRaw and exposes the LibRaw version used by the runtime. The v35 optional runtime is pinned to rawpy 0.27.0, whose release uses LibRaw 0.22.1. Availability of a binary wheel is not treated as proof of qualification: the exact Python/native artifacts are fingerprinted at validation time, and the production Windows wheel/native runtime still needs separate admission on real Windows hardware.

The probe deliberately calls both `rawpy.imread()` and `postprocess()`. Opening alone can establish that a decoder recognizes a container/header but does not prove that the sensor image can be unpacked and rendered. A successful half-size postprocess provides a bounded practical decode-health check while preserving the original file byte-for-byte. The archive records raw/visible/output geometry and exact decoder identity as validation evidence, not as replacement metadata.

Native decoders are isolated from the archive process. A malformed RAW can exercise complex codec/parser code, so the parent launches a fresh subprocess with a hard timeout. Missing rawpy is `unavailable`; decoder exceptions, child crashes, hangs, malformed or contradictory reports are failures. The parent also hashes the media before and after the probe, making source mutation by a supposedly read-only validator an explicit failure.

ExifTool's published RAW read/write capability table remains useful capability information, but it does not by itself prove archival-safe writing for every camera/codec variant. `raw-single-v1` consequently still has no protected-payload fingerprint and no writer round-trip harness. Before RAW writes can be considered, the project still needs format/camera representative fixtures, a protected-payload model that excludes only approved metadata regions, exact writer-candidate evidence, independent decoder validation, and real-camera corpus testing. Until then, preserved RAW bytes remain immutable.

## v36 — Representative public Camera RAW corpus qualification (2026-08-24)

v35 proved that one individual RAW source can be isolated from the archive process, opened and decoded with an exactly fingerprinted rawpy/LibRaw build while preserving source bytes. That still left a coverage gap: a decoder can succeed on one DNG/NEF/CR2 and fail badly on another camera family. v36 therefore adds a separate *corpus* qualification layer rather than treating one successful file as representative.

The canonical corpus manifest is intentionally metadata-only. GPA does not redistribute the sample media and does not infer a license from public availability. It records the raw.pixls.us catalog identity, HTTPS source locations, relative paths and exact SHA-256 values so a future qualification run can acquire samples directly from the source and admit only exact bytes. The public `filelist.sha256` index was rechecked on 2026-08-24 for the selected Canon CR2/CR3, Nikon NEF, Sony ARW, Fujifilm RAF, Panasonic RW2, Olympus ORF, Pentax PEF, Apple DNG, Hasselblad 3FR, Phase One IIQ and Samsung SRW samples.

The corpus gate is fail-closed before native decoding. Manifest traversal, backslash/non-portable paths, extension/family mismatch, duplicate IDs/paths, malformed hashes and non-HTTPS source metadata are rejected. The local sample root cannot contain symlink components for an admitted sample, and every file must be regular and hash to the manifest value before the decoder probe is launched. After the supposedly read-only probe, GPA hashes the source again; mutation is an explicit failure.

Compatibility evidence must also be coherent. Every sample must pass the existing isolated real-decode probe and every passing result must report the *same* rawpy version, LibRaw version and combined Python/native decoder artifact SHA-256. A mixed decoder set or a passing result with missing provenance fails the corpus. Canonical diversity policy currently requires at least 10 samples, 8 brands and 8 format families; the v1 manifest exceeds those floors with 12 samples, 11 brands/device makers and 12 format families.

This is still read-only evidence. Corpus success does not establish that ExifTool or any other tool can safely rewrite CR2, CR3, NEF, ARW, RAF, DNG or other RAW families without disturbing proprietary maker data, sensor data, embedded previews, multi-image structures or camera-private blocks. `raw-single-v1` therefore remains without a protected-payload fingerprint or writer round-trip harness, and production RAW metadata writes remain impossible.

Public corpus source rechecked for this tranche:
- raw.pixls.us public RAW sample archive SHA-256 index, `https://raw.pixls.us/data/filelist.sha256` (redirects to the download catalog). The manifest stores only file identities/paths and does not bundle media.


## v37 — Fail-closed acquisition of the exact public RAW corpus (2026-08-24)

v36 intentionally did not bundle public Camera RAW samples, which preserved licensing/provenance boundaries but left a practical qualification step: obtaining the exact bytes safely. v37 adds an acquisition layer rather than silently trusting whatever file happens to exist under a sample filename.

Before downloading any sample, GPA retrieves the manifest's HTTPS SHA-256 index and verifies that every selected public path still maps to the manifest's exact pinned digest. A stale, malformed or conflicting source index blocks all sample transfers. The source path is percent-encoded without normalizing away manifest structure, and HTTPS is required both for the requested URL and any final redirect.

Sample bytes are streamed into a temporary file in the destination directory, hashed during transfer, flushed and fsynced, compared to the pinned SHA-256, then committed with the existing no-overwrite transaction helper. The committed file is hashed again. Existing exact-hash files are reusable; existing mismatches, symlinks and non-regular files are failures and are never replaced. Failed transfers remove staging files. This means the acquisition helper can populate a qualification corpus while maintaining the project's core rule that unknown/conflicting bytes are never overwritten.

Passing acquisition is not RAW compatibility evidence by itself. The separately invoked v36 corpus qualification still has to decode every admitted exact file through one exact rawpy/LibRaw identity without changing bytes. Neither acquisition nor read-only decode qualification authorizes embedded RAW writes.

## v38 — Consolidated Windows core qualification and exact fixture acquisition (2026-08-25)

The remaining real-machine boundary had become fragmented across separate commands: Windows disk topology, public RAW corpus acquisition, and read-only rawpy/LibRaw corpus qualification. v38 introduces a deliberately narrow orchestrator rather than broadening any production permission. `gpa.windows-core-qualification.v1` records Windows/x64 platform identity, fingerprints the exact active Python executable where possible, requires the nominated primary and backup roots to exist as real directories, evaluates them through the established Windows volume-extents plus storage-device-descriptor model, and binds the representative RAW corpus qualification report. A passing core report therefore requires both machine-proven storage independence and one exact decoder build across exact unchanged RAW bytes.

The command can optionally invoke v37's public RAW fetcher first, but a successful network transfer is not substituted for decode evidence. If the exact sample bytes already exist, acquisition may be skipped and the local corpus still has to pass all manifest, diversity, decode, identity and source-immutability checks. Any platform, path, storage, fetch or RAW qualification failure is retained as a failed check; the orchestrator never converts manual assertion into Windows hardware proof.

The exact HEIC qualification fixture previously had strict admission but still needed to be obtained outside the program. v38 adds an acquisition helper with the same preservation posture used elsewhere: HTTPS before and after redirects, a hard byte ceiling equal to the pinned size, exact SHA-256 and Git-blob verification before commit, fsync, no-overwrite atomic commit, and exact post-commit verification. Existing matching bytes are reused; existing conflicting bytes block the operation and remain untouched.

Release-gate integrity itself is now checked. Because v37 added a new test module, an omission in the static module tuple could have allowed a future complete-suite tool to claim success while silently skipping a newly created `tests/test_*.py`. v38 adds a regression that compares the release registry to the actual test-module set. The registry now includes all 23 release modules.

ExifTool upstream was rechecked during this tranche. SourceForge still lists 13.59 (2026-05-27) as the latest release, so GPA's already pinned 13.59 Windows x64 candidate remains current; no new production approval is implied. The exact Windows candidate still has to run the complete build/format/relationship evidence chain on real Windows before any allow-list decision can even be reviewed.

## v39 — Consolidated Windows release evidence session (2026-08-28)

v38 reduced the physical-PC boundary to a single Windows core command, but exact ExifTool base evidence still required a separate multi-command sequence. v39 adds a higher-level fail-closed session that first requires the complete Windows core gate and then admits one exact pinned Windows x64 ExifTool distribution, prepares its isolated distribution tree, runs the established JPEG payload/decode/readback harness, and constructs a base evidence bundle bound to the exact live archive, executable and distribution fingerprints.

The session does not promote anything. Its report explicitly records `format_writer_qualification_complete=false`, `relationship_writer_qualification_complete=false`, `production_write_approved=false`, and `google_retirement_approved=false`. Per-format JPEG/PNG/QuickTime/HEIC/AVIF evidence and Live/Motion Photo relationship evidence remain independent review gates. This is deliberate separation between collecting a coherent real-machine evidence packet and making a production governance decision.

## v40 — Same-candidate per-format and relationship writer-review evidence (2026-08-28)

v39 deliberately stopped at Windows core plus exact ExifTool base-build evidence. That left the eventual real-PC workflow fragmented again for per-format and composite relationship proof. v40 adds a review orchestrator rather than changing any production policy.

The new writer-review session refuses to begin unless the complete v39 Windows release gate passes. It then treats the live ExifTool archive, executable and prepared distribution as evidence inputs that must still match the base bundle. For each selected standalone profile it invokes the existing format-specific round-trip harness and builds the corresponding exact-candidate format evidence bundle. Composite Motion Photo and Live Photo harnesses are run only when the necessary component format evidence was produced in that same session. This prevents evidence from different ExifTool builds or separate ad-hoc runs being silently combined.

HEIC remains exact-fixture bound. The fixture may be safely acquired through the v38 helper or reused only if its pinned size/SHA-256/Git-blob identity verifies. RAW remains excluded because there is still no RAW writer payload-preservation harness.

A passing `gpa.windows-writer-qualification.v1` report means the evidence chain is ready for governance review. It never mutates `PRODUCTION_APPROVED_BUILDS` or `PRODUCTION_FORMAT_APPROVALS`, and both production-write and Google-retirement booleans remain false by construction. Genuine Apple Photos acceptance remains a separate compatibility gate even if the Live Photo relationship harness passes.


## v41 candidate — 2026-08-29 storage durability hardening

Review of the v40 transaction layer found two related fail-closed gaps. First, directory fsync suppressed every `OSError`, so a real ENOSPC/EIO after a namespace change could be reported as durable. Second, `commit_no_overwrite` wrapped both `os.link` and the subsequent directory fsync in the same exception path, allowing a post-link durability failure to be misclassified as a reason to attempt copy fallback.

v41 candidate narrows the portability exception to explicit unsupported directory-fsync errors after the directory has been opened. Real storage errors propagate. Hardlink creation and durability confirmation are now separate phases: unsupported/cross-device link failures may use exclusive-create copy fallback, while storage errors fail closed without a second write attempt. If the hardlink itself succeeded but directory durability confirmation fails, the unconfirmed destination is removed where possible and the staged bytes are retained.

Revision pointer updates were also moved from a predictable `.current-<pid>.tmp` file to the same unique fsynced staging primitive used elsewhere. This removes stale-temp collision risk and gives pointer writes consistent cleanup/retry behavior. A failure before replacement leaves the old current pointer intact; a failure after replacement can only point at a pre-existing immutable revision and is safe to rerun.

Nine regressions cover staging ENOSPC cleanup, copy-fallback partial cleanup, directory-fsync failure propagation, unsupported directory-fsync tolerance, hardlink ENOSPC fallback suppression, post-link durability rollback, and revision-pointer failure/retry behavior. The focused core/hardening/backup subset passes 311/311; backup fault qualification passes 12/12; mixed-media incremental qualification passes 15/15; the additional crash-recovery stress tool passes 13/13. Full candidate promotion remains blocked until a complete 584/584 release run can be reproduced from the exact candidate checkpoint.


## v42 candidate — 2026-08-29 copy-derived retryability hardening

After v41 made generic commit failures retain staged bytes for recovery, review of Source Vault and mirror callers identified an important distinction: their staging files are derived copies of still-authoritative source files and are not consumed by any existing resume mechanism. Leaving those stages behind after ENOSPC/EIO therefore does not improve recovery and can consume destination capacity on each retry. v42 cleans those copy-derived stages on commit failure without masking the original exception.

A second retryability gap existed after commit: if a newly created vault or mirror object failed its immediate hash/read verification, it remained at the final path. The next run then encountered a content-address or destination conflict and could not retry automatically. v42 removes only newly-created, not-yet-accepted destinations after verification failure. Existing/reused destinations are never deleted by this branch. Raw Takeout/source bytes remain untouched and authoritative.

Five regressions cover Source Vault commit ENOSPC cleanup, mirror commit ENOSPC cleanup, mirror stage-copy EIO cleanup, Source Vault post-commit verification cleanup, and mirror post-commit verification cleanup. Focused core/hardening/recovery passes 316/316; backup fault qualification passes 12/12; mixed-media qualification passes 15/15.


## 2026-08-29 — v43 candidate durability/audit tranche

Building from authoritative v42 / package 0.0.29. v43 hardens provenance-record durability cleanup and converts device-read errors during Source Vault/mirror verification into explicit failed evidence rather than uncaught audit termination. No writer approval, RAW rewriting, or Google-retirement gate is changed.


## 2026-08-29 — v44 candidate audit-enumeration tranche

Building from authoritative v43. v44 converts Source Vault directory-enumeration EIO during provenance-record or archive-tree traversal into explicit audit problems rather than uncaught termination. No writer or retirement permission changes.


## 2026-08-29 — v45 candidate archive-audit enumeration tranche

Building from authoritative v44. Archive audit now converts asset, revision, blob, identity-observation, identity-sighting, and run-manifest directory traversal EIO into explicit audit problems rather than uncaught termination. No writer or retirement permission changes.


## v46 — Audit evidence for mid-file storage read loss (2026-08-29)

Directory traversal can succeed and a device can still disappear or return EIO while SHA-256 is reading a specific file. Archive audit now converts current-media, current-XMP, historical blob, and preserved-unclassified hash-read failures into explicit problems instead of terminating the audit. Four direct regressions cover these boundaries. Production writer and retirement gates are unchanged.

2026-08-30 — v48 candidate hidden-stat/read-fault tranche
Built only from promoted v47. The audit no longer relies on pathlib is_dir/is_file behavior at asset/blob entry boundaries where OSError may be suppressed as False. Current revision read I/O failure also becomes explicit evidence instead of a silent continue. Production safety remains unchanged.


2026-08-30 — v49 candidate read-I/O classification tranche
Archive audit now distinguishes OSError/device read failure from genuine parse corruption for current-pointer JSON, immutable revision JSON, XMP XML, identity observation/sighting JSON, and run manifests. Six direct regressions added. Production writer and retirement policy unchanged.


2026-08-30 — v50 candidate run-manifest structural hardening
Audit now handles syntactically valid but structurally invalid run manifests without crashing. Non-object manifests, non-array unclassified_preserved fields, and non-object rows become explicit audit problems while valid rows continue to be verified. Three direct regressions added.

## v51 — Product phase / evidence-bound operator workflow (2026-08-30)

The preservation engine is mature enough that product orchestration is now higher value than additional speculative filesystem wrappers. v51 adds a separate product layer rather than moving preservation logic into a GUI. Source Takeout ZIPs are fingerprinted and re-fingerprinted around qualification; representative Takeout evidence is bound to the exact session hash/size multiset; arbitrary JSON claiming `passed=true` is not sufficient for Windows gates; unresolved source understanding and unclassified material fail representative qualification; and migration success cannot make the retirement dashboard green without the external evidence gates.

## v52 — 2026-08-30 — real-world qualification preparation
- Added `gpa.realworld` with a session-bound Windows qualification plan, parameterized PowerShell runner, read-only host diagnostics and privacy support bundle.
- Handoff preparation never counts as Windows evidence and never approves metadata writing or Google retirement.
- Support bundles use a strict allow-list and exclude source paths, filenames, Takeout/media bytes, notes and free-form error/blocker text. Exact source hashes are opt-in.
- Added CLI commands: `prepare-real-world`, `host-diagnostics`, `privacy-support-bundle`.
- Added two registered release-test modules for handoff security/privacy and CLI integration.
