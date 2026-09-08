# Windows-qualified v52 software checkpoint — 2026-09-08

## Authority record

This is the replacement restoration authority for the **software-only** v52
Windows-qualified state.  It was produced from exact Git commit
`112b287e81b7527618d7a2ebf1ae3e5505822dbb` on
`codex/v52-windows-qualification-repair-20260908`.

- Checkpoint filename:
  `google_photos_archive_lab_checkpoint_20260908_v52_windows_qualified_112b287.zip`
- Checkpoint location:
  `C:\Users\steph\Dropbox\Projects\Google Photos Archive Lab\`
- SHA-256:
  `9702C73B85E5EF07949E26E45FE20C5C179808D88A9BC48D5ACBF545E18912DE`
- ZIP integrity: verified by .NET `ZipFile` entry enumeration and `tar -tf`
  listing (196 archive entries).

The historical checkpoint remains preserved, and is superseded only for
software restoration:

- `google_photos_archive_lab_checkpoint_20260830_v52.zip`
- SHA-256:
  `3714bf5dbededf9cdf37b006cb18b3eaf491fdbc60c9a37fb8a9a087b0412973`

## Verification evidence

All runs used Python 3.12.14 in the isolated verification environment with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, pytest cache disabled, and distinct
disposable `D:\gpa-pytest-*` roots.  FFmpeg 9.0.1 and ffprobe 9.0.1 were on
the test process PATH.  Positive AVIF decoder tests remained intentionally
skipped because the pinned `GPA_HEIF_CONVERT` capability was not installed;
FFmpeg alone is not accepted as evidence of that capability.

| Verification | Tests | Failures | Errors | Skipped | Timeouts |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fresh repair run 1 | 671 | 0 | 0 | 15 | 0 |
| Independent fresh repair run 2 | 671 | 0 | 0 | 15 | 0 |
| Fresh checkpoint extraction | 671 | 0 | 0 | 15 | 0 |

The skips are recorded capability/fixture limits, including Windows hosts
without real-symlink creation privilege.  They do not conceal a failed
assertion.  The full reconciliation of the earlier uncorroborated 671-pass
claim is retained in `QUALIFICATION_EVIDENCE_RECONCILIATION_20260908.md`.

## Scope boundary

This checkpoint closes the bounded software-qualification gate only.  Before
the first read-only real Takeout qualification, follow
`TAKEOUT_QUALIFICATION_HANDOFF.md`; obtain an explicit, separately scoped
authorization and retain the resulting evidence.  Nothing in this record
authorizes personal-photo/Takeout inspection, metadata writing, RAW rewrite,
reconstruction, upload, or Google deletion.
