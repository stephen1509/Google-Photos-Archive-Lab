#!/usr/bin/env python3
"""Capture Windows physical-storage independence evidence for two archive paths."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / 'src'))

from gpa.storage_identity import assess_storage_independence

SCHEMA = "gpa.windows-storage-qualification.v2"


def qualify(primary: Path, secondary: Path) -> dict:
    assessment = assess_storage_independence(primary, secondary)
    passed = bool(
        platform.system() == "Windows"
        and assessment.separate_physical_device_proven
        and assessment.separate_physical_media_confirmed
        and assessment.independence_evidence_source == "windows_disk_extents_and_storage_device_descriptor"
    )
    return {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "platform_system": platform.system(),
        "primary": str(Path(primary)),
        "secondary": str(Path(secondary)),
        "assessment": assessment.to_dict(),
        "passed": passed,
        "result": (
            "separate_physical_devices_proven"
            if passed
            else "same_physical_device_detected"
            if assessment.same_physical_device is True
            else "physical_device_independence_unproven"
        ),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("primary", type=Path)
    ap.add_argument("secondary", type=Path)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    result = qualify(a.primary, a.secondary)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        if a.report.exists():
            raise FileExistsError(a.report)
        a.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
