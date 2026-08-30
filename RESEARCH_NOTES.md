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

## 2026-08-22 — archive-format and validator provenance tranche
- Revalidated Google Photos' current documented backup file families against official Google Photos Help. Photos: JPG, HEIC/HEIF, PNG, WebP, GIF, AVIF and most RAW; videos include MPG/MOD/MMV/TOD/WMV/ASF/AVI/DIVX/MOV/M4V/3GP/3G2/MP4/M2T/M2TS/MTS/MKV.
- Added frozen archive-format manifest `gpa.archive.v1`. Normal planning/preflight remains read-only; first execution initializes it. Unknown/future formats fail closed. Existing unversioned archives require explicit metadata-only upgrade.
- Added qualified-media validation records with explicit status (`passed`, `failed`, `unavailable`), validator name, exact version, and details.
- Current qualified lanes: Pillow still images; libheif HEIC/HEIF/AVIF when decode tools are available; ffprobe generic video requiring an actual video stream. RAW remains explicitly `unavailable` until a qualified RAW/ExifTool lane exists.
- Independent audit now rejects a claim of `passed` validation that lacks concrete validator/version provenance.
- Migration verification now treats incomplete/failed media validation and missing/unsupported archive-format manifests as Google-retirement blockers.
- Real AVIF full-pipeline qualification added in the local environment using FFmpeg-generated AVIF + libheif decode. HEIC encode integration test remains conditional because this environment lacks a HEVC encoder plugin for `heif-enc`; corrupt/fake HEIC is never treated as validator-unavailable when libheif is present.
