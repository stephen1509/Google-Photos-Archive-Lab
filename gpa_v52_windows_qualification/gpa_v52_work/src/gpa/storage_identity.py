from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import platform


@dataclass(frozen=True)
class StorageIdentityAssessment:
    schema: str = "gpa.storage-identity.v4"
    assessed_at_utc: str = ""
    platform_system: str = ""
    identity_method: str = "os.stat.st_dev(existing_ancestor)"
    physical_identity_method: str | None = None
    primary_path: str = ""
    secondary_path: str = ""
    primary_resolved_path: str = ""
    secondary_resolved_path: str = ""
    primary_existing_ancestor: str | None = None
    secondary_existing_ancestor: str | None = None
    primary_device: str | None = None
    secondary_device: str | None = None
    same_resolved_path: bool = False
    same_volume: bool | None = None
    separate_volume_proven: bool = False
    primary_physical_disks: tuple[int, ...] = ()
    secondary_physical_disks: tuple[int, ...] = ()
    separate_disk_numbers_proven: bool = False
    primary_physical_serials: tuple[str, ...] = ()
    secondary_physical_serials: tuple[str, ...] = ()
    same_physical_device: bool | None = None
    physical_identity_conflict: bool = False
    separate_physical_device_proven: bool = False
    primary_windows_topology: dict | None = None
    secondary_windows_topology: dict | None = None
    separate_physical_media_confirmation_requested: bool = False
    separate_physical_media_confirmed: bool = False
    confirmation_source: str | None = None
    independence_evidence_source: str | None = None
    note: str = ""

    def to_dict(self):
        return asdict(self)


def _existing_ancestor(path: Path) -> Path | None:
    p = Path(path)
    while True:
        if p.exists():
            return p
        parent = p.parent
        if parent == p:
            return None
        p = parent


def _device_token(path: Path) -> str | None:
    p = _existing_ancestor(Path(path))
    if p is None:
        return None
    try:
        st = os.stat(p)
    except OSError:
        return None
    return str(getattr(st, "st_dev", "")) or None


def _resolved_text(path: Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


def _windows_volume_topology(path: Path):
    from .windows_storage import probe_windows_volume

    return probe_windows_volume(path)


def assess_storage_independence(
    primary: Path,
    secondary: Path,
    *,
    separate_physical_media_confirmed: bool = False,
) -> StorageIdentityAssessment:
    primary = Path(primary)
    secondary = Path(secondary)
    system = platform.system()
    primary_resolved = _resolved_text(primary)
    secondary_resolved = _resolved_text(secondary)
    same_path = primary_resolved == secondary_resolved
    pa = _existing_ancestor(primary)
    sa = _existing_ancestor(secondary)
    a = _device_token(primary)
    b = _device_token(secondary)
    same_volume = None if a is None or b is None else a == b
    separate_volume = bool(a is not None and b is not None and a != b)

    primary_topology = None
    secondary_topology = None
    primary_disks: tuple[int, ...] = ()
    secondary_disks: tuple[int, ...] = ()
    primary_serials: tuple[str, ...] = ()
    secondary_serials: tuple[str, ...] = ()
    same_physical: bool | None = None
    separate_disk_numbers = False
    identity_conflict = False
    separate_physical = False
    physical_method = None

    if system == "Windows":
        physical_method = (
            "windows:IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS+"
            "IOCTL_STORAGE_QUERY_PROPERTY(StorageDeviceProperty)"
        )
        p_probe_path = Path(pa).resolve() if pa is not None else Path(primary_resolved)
        s_probe_path = Path(sa).resolve() if sa is not None else Path(secondary_resolved)
        ptop = _windows_volume_topology(p_probe_path)
        stop = _windows_volume_topology(s_probe_path)
        primary_topology = ptop.to_dict()
        secondary_topology = stop.to_dict()
        if ptop.topology_supported:
            primary_disks = tuple(int(x) for x in ptop.disk_numbers)
        if stop.topology_supported:
            secondary_disks = tuple(int(x) for x in stop.disk_numbers)
        primary_serials = tuple(
            sorted(
                str(i.serial_number)
                for i in ptop.disk_identities
                if i.hardware_identity_eligible and i.serial_number
            )
        )
        secondary_serials = tuple(
            sorted(
                str(i.serial_number)
                for i in stop.disk_identities
                if i.hardware_identity_eligible and i.serial_number
            )
        )
        if primary_disks and secondary_disks:
            disk_overlap = set(primary_disks) & set(secondary_disks)
            same_physical = bool(disk_overlap)
            separate_disk_numbers = not disk_overlap
            primary_serial_keys = {x.casefold() for x in primary_serials}
            secondary_serial_keys = {x.casefold() for x in secondary_serials}
            serial_overlap = primary_serial_keys & secondary_serial_keys
            identity_conflict = bool(serial_overlap)
            # Disk numbers alone are not sufficient because Windows can expose
            # VHD, iSCSI, Storage Spaces and other non-physical/aggregate devices
            # as disks. Automatic proof requires eligible direct/local device
            # descriptors, usable serials, and no identity overlap.
            separate_physical = bool(
                separate_disk_numbers
                and ptop.hardware_identity_supported
                and stop.hardware_identity_supported
                and len(primary_serials) == len(primary_disks)
                and len(secondary_serials) == len(secondary_disks)
                and len(primary_serial_keys) == len(primary_disks)
                and len(secondary_serial_keys) == len(secondary_disks)
                and not serial_overlap
            )

    confirmation_requested = bool(separate_physical_media_confirmed)
    contradictory_machine_evidence = bool(
        same_path or same_volume is True or same_physical is True or identity_conflict
    )

    if separate_physical:
        confirmation_effective = True
        evidence_source = "windows_disk_extents_and_storage_device_descriptor"
    elif confirmation_requested and not contradictory_machine_evidence:
        confirmation_effective = True
        evidence_source = "explicit_user_or_operator_input"
    else:
        confirmation_effective = False
        evidence_source = None

    if same_path:
        note = "Primary and redundant paths resolve to the same location; physical-media confirmation is invalid."
    elif same_physical is True:
        overlap = sorted(set(primary_disks) & set(secondary_disks))
        note = (
            "Windows disk topology shows the paths depend on overlapping disk number(s) "
            f"{overlap}; this is not independent storage and user confirmation cannot override that evidence."
        )
    elif identity_conflict:
        overlap = sorted(set(primary_serials) & set(secondary_serials))
        note = (
            "Windows storage-device descriptors report overlapping device serial(s) "
            f"{overlap} despite different disk numbers; automatic/manual physical-media confirmation is blocked "
            "until that identity conflict is resolved."
        )
    elif same_volume is True:
        note = (
            "Paths are on the same detected filesystem/volume; this is not independent "
            "storage and user confirmation cannot override that evidence."
        )
    elif separate_physical:
        note = (
            "Windows disk extents plus eligible direct/local storage-device descriptors with "
            "distinct serials prove disjoint device identities for the primary and redundant paths."
        )
    elif separate_disk_numbers and system == "Windows":
        detail = []
        for label, topo in (("primary", primary_topology), ("secondary", secondary_topology)):
            if topo and not topo.get("hardware_identity_supported"):
                reason = topo.get("hardware_identity_error") or topo.get("error") or "device identity is not eligible"
                detail.append(f"{label}: {reason}")
        prefix = (
            "Windows reports disjoint disk numbers, but disk numbers alone do not prove separate physical hardware. "
            "Automatic proof is withheld until each underlying disk has an eligible direct/local bus and usable serial."
        )
        note = prefix + ((" Detail: " + "; ".join(detail)) if detail else "")
        if confirmation_effective:
            note += " Separate physical media was explicitly confirmed without contradictory machine evidence."
    elif separate_volume and confirmation_effective:
        note = (
            "Paths are on different detected filesystems/volumes and separate physical media was explicitly "
            "confirmed; automatic physical-device identity was not proven."
        )
    elif separate_volume:
        note = (
            "Paths are on different detected filesystems/volumes. Physical-device independence is not yet proven."
        )
    elif confirmation_effective:
        note = (
            "Filesystem/volume independence could not be proven automatically; separate physical media was "
            "explicitly confirmed without contradictory machine evidence."
        )
    else:
        note = "Filesystem/volume and physical-device independence could not be proven automatically."
        if system == "Windows":
            errors = []
            for label, topo in (("primary", primary_topology), ("secondary", secondary_topology)):
                if topo and topo.get("error"):
                    errors.append(f"{label}: {topo['error']}")
            if errors:
                note += " Windows topology detail: " + "; ".join(errors)

    return StorageIdentityAssessment(
        assessed_at_utc=datetime.now(timezone.utc).isoformat(),
        platform_system=system,
        physical_identity_method=physical_method,
        primary_path=str(primary),
        secondary_path=str(secondary),
        primary_resolved_path=primary_resolved,
        secondary_resolved_path=secondary_resolved,
        primary_existing_ancestor=str(pa) if pa is not None else None,
        secondary_existing_ancestor=str(sa) if sa is not None else None,
        primary_device=a,
        secondary_device=b,
        same_resolved_path=same_path,
        same_volume=same_volume,
        separate_volume_proven=separate_volume,
        primary_physical_disks=primary_disks,
        secondary_physical_disks=secondary_disks,
        separate_disk_numbers_proven=separate_disk_numbers,
        primary_physical_serials=primary_serials,
        secondary_physical_serials=secondary_serials,
        same_physical_device=same_physical,
        physical_identity_conflict=identity_conflict,
        separate_physical_device_proven=separate_physical,
        primary_windows_topology=primary_topology,
        secondary_windows_topology=secondary_topology,
        separate_physical_media_confirmation_requested=confirmation_requested,
        separate_physical_media_confirmed=confirmation_effective,
        confirmation_source="explicit_user_or_operator_input" if confirmation_requested else None,
        independence_evidence_source=evidence_source,
        note=note,
    )
