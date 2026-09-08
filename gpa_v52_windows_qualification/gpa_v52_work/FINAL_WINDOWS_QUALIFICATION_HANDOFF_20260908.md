# Final Windows-qualified software handoff — 2026-09-08

## Restoration authority

- Historical authority retained:
  `google_photos_archive_lab_checkpoint_20260830_v52.zip`
  - SHA-256:
    `3714bf5dbededf9cdf37b006cb18b3eaf491fdbc60c9a37fb8a9a087b0412973`
- New Windows-qualified software authority:
  `google_photos_archive_lab_checkpoint_20260908_v52_windows_qualified_112b287.zip`
  - SHA-256:
    `9702C73B85E5EF07949E26E45FE20C5C179808D88A9BC48D5ACBF545E18912DE`

The older checkpoint is preserved.  The newer checkpoint supersedes it for
software restoration only.

## Git provenance

- Repair/source checkpoint commit:
  `112b287e81b7527618d7a2ebf1ae3e5505822dbb`
- Authority/status record commit:
  `4ef256a0b45324644987a8fbc4618df9de3a2d12`
- Branch: `codex/v52-windows-qualification-repair-20260908`
- `main` remains untouched at `e18ac5ef467f3b626b1343fd310046d5a0462dcd`.

## Software-only verification evidence

All runs used Python 3.12.14, isolated test state, plugin autoload disabled,
pytest cache disabled, and a fresh disposable `D:\gpa-pytest-*` root.

| Run | Tests | Failed | Errors | Skipped | Timed out |
| --- | ---: | ---: | ---: | ---: | ---: |
| Repair run 1 | 671 | 0 | 0 | 15 | 0 |
| Independent repair run 2 | 671 | 0 | 0 | 15 | 0 |
| Fresh extracted checkpoint | 671 | 0 | 0 | 15 | 0 |

Intentional skips are explicit capability/fixture limits: Windows hosts where
real symbolic-link creation is denied, positive AVIF decoder checks without
the pinned `GPA_HEIF_CONVERT` capability, and existing unavailable optional
fixture/corpus capabilities.  They are not assertion failures or waived test
failures.

## Exact next step and safety boundary

The next action is the first **read-only real Takeout qualification** under
`TAKEOUT_QUALIFICATION_HANDOFF.md`, with separately scoped explicit approval.
This handoff does not authorize metadata writes, RAW rewriting, deletion,
Google retirement, reconstruction, upload, or any other modification of
personal media.
