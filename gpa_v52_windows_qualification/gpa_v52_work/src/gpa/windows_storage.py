from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import ctypes
from ctypes import wintypes
import platform


IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
ERROR_MORE_DATA = 234
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6
_DRIVE_TYPE_NAMES = {
    DRIVE_UNKNOWN: "unknown",
    DRIVE_NO_ROOT_DIR: "no_root_dir",
    DRIVE_REMOVABLE: "removable",
    DRIVE_FIXED: "fixed",
    DRIVE_REMOTE: "remote",
    DRIVE_CDROM: "cdrom",
    DRIVE_RAMDISK: "ramdisk",
}

# STORAGE_BUS_TYPE values from winioctl.h / ntddstor.h.
BUS_UNKNOWN = 0
BUS_SCSI = 1
BUS_ATAPI = 2
BUS_ATA = 3
BUS_1394 = 4
BUS_SSA = 5
BUS_FIBRE = 6
BUS_USB = 7
BUS_RAID = 8
BUS_ISCSI = 9
BUS_SAS = 10
BUS_SATA = 11
BUS_SD = 12
BUS_MMC = 13
BUS_VIRTUAL = 14
BUS_FILE_BACKED_VIRTUAL = 15
BUS_SPACES = 16
BUS_NVME = 17
BUS_SCM = 18
BUS_UFS = 19
BUS_NVMEOF = 20
_BUS_TYPE_NAMES = {
    BUS_UNKNOWN: "unknown",
    BUS_SCSI: "scsi",
    BUS_ATAPI: "atapi",
    BUS_ATA: "ata",
    BUS_1394: "ieee1394",
    BUS_SSA: "ssa",
    BUS_FIBRE: "fibre_channel",
    BUS_USB: "usb",
    BUS_RAID: "raid",
    BUS_ISCSI: "iscsi",
    BUS_SAS: "sas",
    BUS_SATA: "sata",
    BUS_SD: "sd",
    BUS_MMC: "mmc",
    BUS_VIRTUAL: "virtual",
    BUS_FILE_BACKED_VIRTUAL: "file_backed_virtual",
    BUS_SPACES: "storage_spaces",
    BUS_NVME: "nvme",
    BUS_SCM: "storage_class_memory",
    BUS_UFS: "ufs",
    BUS_NVMEOF: "nvme_over_fabrics",
}

# Only direct/local device classes are eligible for automatic *physical-media*
# proof. Ambiguous virtual, network, aggregate, SAN, RAID and generic SCSI buses
# retain disk-number evidence but require independent/manual evidence.
_DIRECT_LOCAL_BUS_TYPES = frozenset(
    {
        BUS_ATAPI,
        BUS_ATA,
        BUS_1394,
        BUS_USB,
        BUS_SAS,
        BUS_SATA,
        BUS_SD,
        BUS_MMC,
        BUS_NVME,
        BUS_SCM,
        BUS_UFS,
    }
)


@dataclass(frozen=True)
class DiskExtent:
    disk_number: int
    starting_offset: int
    extent_length: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PhysicalDiskIdentity:
    schema: str = "gpa.windows-physical-disk-identity.v1"
    disk_number: int = -1
    bus_type: int | None = None
    bus_type_name: str | None = None
    serial_number: str | None = None
    vendor_id: str | None = None
    product_id: str | None = None
    product_revision: str | None = None
    removable_media: bool | None = None
    descriptor_supported: bool = False
    hardware_identity_eligible: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WindowsVolumeTopology:
    schema: str = "gpa.windows-volume-topology.v2"
    path: str = ""
    volume_mount_point: str | None = None
    volume_guid_path: str | None = None
    drive_type: int | None = None
    drive_type_name: str | None = None
    local_volume: bool = False
    topology_supported: bool = False
    disk_numbers: tuple[int, ...] = ()
    disk_extents: tuple[DiskExtent, ...] = ()
    disk_identities: tuple[PhysicalDiskIdentity, ...] = ()
    hardware_identity_supported: bool = False
    hardware_identity_error: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class WindowsVolumeAPI:
    """Small injectable interface around the Win32 calls used by the probe."""

    def get_volume_path_name(self, path: str) -> str:  # pragma: no cover - interface only
        raise NotImplementedError

    def get_drive_type(self, volume_mount_point: str) -> int:  # pragma: no cover - interface only
        raise NotImplementedError

    def get_volume_name(self, volume_mount_point: str) -> str:  # pragma: no cover - interface only
        raise NotImplementedError

    def get_disk_extents(self, volume_guid_path: str) -> tuple[DiskExtent, ...]:  # pragma: no cover - interface only
        raise NotImplementedError

    def get_disk_identity(self, disk_number: int) -> PhysicalDiskIdentity:  # pragma: no cover - interface only
        raise NotImplementedError


class _DISK_EXTENT(ctypes.Structure):
    _fields_ = [
        ("DiskNumber", wintypes.DWORD),
        ("StartingOffset", ctypes.c_longlong),
        ("ExtentLength", ctypes.c_longlong),
    ]


class _VOLUME_DISK_EXTENTS_ONE(ctypes.Structure):
    _fields_ = [
        ("NumberOfDiskExtents", wintypes.DWORD),
        ("Extents", _DISK_EXTENT * 1),
    ]


class _STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [
        ("PropertyId", wintypes.DWORD),
        ("QueryType", wintypes.DWORD),
        ("AdditionalParameters", ctypes.c_ubyte * 1),
    ]


class _STORAGE_DESCRIPTOR_HEADER(ctypes.Structure):
    _fields_ = [("Version", wintypes.DWORD), ("Size", wintypes.DWORD)]


class _STORAGE_DEVICE_DESCRIPTOR_FIXED(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("DeviceType", ctypes.c_ubyte),
        ("DeviceTypeModifier", ctypes.c_ubyte),
        ("RemovableMedia", ctypes.c_ubyte),
        ("CommandQueueing", ctypes.c_ubyte),
        ("VendorIdOffset", wintypes.DWORD),
        ("ProductIdOffset", wintypes.DWORD),
        ("ProductRevisionOffset", wintypes.DWORD),
        ("SerialNumberOffset", wintypes.DWORD),
        ("BusType", wintypes.DWORD),
        ("RawPropertiesLength", wintypes.DWORD),
    ]


_EXTENTS_OFFSET = _VOLUME_DISK_EXTENTS_ONE.Extents.offset
_EXTENT_SIZE = ctypes.sizeof(_DISK_EXTENT)
_DEVICE_DESCRIPTOR_MIN_SIZE = ctypes.sizeof(_STORAGE_DEVICE_DESCRIPTOR_FIXED)


def _parse_disk_extents_buffer(raw: bytes) -> tuple[DiskExtent, ...]:
    """Parse a VOLUME_DISK_EXTENTS buffer using the platform ABI alignment."""
    if len(raw) < _EXTENTS_OFFSET:
        raise ValueError("volume disk extents buffer is too short")
    count = int.from_bytes(raw[:4], "little", signed=False)
    if count <= 0:
        raise ValueError("volume disk extents returned no extents")
    needed = _EXTENTS_OFFSET + count * _EXTENT_SIZE
    if len(raw) < needed:
        raise ValueError(f"volume disk extents buffer is truncated: need {needed}, have {len(raw)}")
    result: list[DiskExtent] = []
    for i in range(count):
        start = _EXTENTS_OFFSET + i * _EXTENT_SIZE
        c_extent = _DISK_EXTENT.from_buffer_copy(raw[start : start + _EXTENT_SIZE])
        result.append(
            DiskExtent(
                disk_number=int(c_extent.DiskNumber),
                starting_offset=int(c_extent.StartingOffset),
                extent_length=int(c_extent.ExtentLength),
            )
        )
    return tuple(result)


def _descriptor_ascii(raw: bytes, offset: int) -> str | None:
    if not offset:
        return None
    if offset < 0 or offset >= len(raw):
        raise ValueError(f"storage descriptor string offset {offset} is outside {len(raw)} bytes")
    end = raw.find(b"\0", offset)
    if end < 0:
        raise ValueError(f"storage descriptor string at offset {offset} is not NUL terminated")
    text = raw[offset:end].decode("ascii", errors="replace").strip()
    return text or None


def _parse_storage_device_descriptor(raw: bytes, disk_number: int) -> PhysicalDiskIdentity:
    if len(raw) < _DEVICE_DESCRIPTOR_MIN_SIZE:
        raise ValueError(
            f"storage device descriptor is truncated: need {_DEVICE_DESCRIPTOR_MIN_SIZE}, have {len(raw)}"
        )
    fixed = _STORAGE_DEVICE_DESCRIPTOR_FIXED.from_buffer_copy(raw[:_DEVICE_DESCRIPTOR_MIN_SIZE])
    declared = int(fixed.Size)
    if declared < _DEVICE_DESCRIPTOR_MIN_SIZE or declared > len(raw):
        raise ValueError(f"storage device descriptor declared invalid size {declared} for {len(raw)} bytes")
    view = raw[:declared]
    bus_type = int(fixed.BusType)
    bus_name = _BUS_TYPE_NAMES.get(bus_type, f"unknown_{bus_type}")
    serial = _descriptor_ascii(view, int(fixed.SerialNumberOffset))
    vendor = _descriptor_ascii(view, int(fixed.VendorIdOffset))
    product = _descriptor_ascii(view, int(fixed.ProductIdOffset))
    revision = _descriptor_ascii(view, int(fixed.ProductRevisionOffset))
    eligible = bus_type in _DIRECT_LOCAL_BUS_TYPES and bool(serial)
    if bus_type not in _DIRECT_LOCAL_BUS_TYPES:
        error = f"bus type {bus_name} is not eligible for automatic physical-hardware proof"
    elif not serial:
        error = "storage device descriptor has no usable serial number"
    else:
        error = None
    return PhysicalDiskIdentity(
        disk_number=int(disk_number),
        bus_type=bus_type,
        bus_type_name=bus_name,
        serial_number=serial,
        vendor_id=vendor,
        product_id=product,
        product_revision=revision,
        removable_media=bool(fixed.RemovableMedia),
        descriptor_supported=True,
        hardware_identity_eligible=eligible,
        error=error,
    )


class CtypesWindowsVolumeAPI(WindowsVolumeAPI):
    """Win32 volume and underlying-device topology adapter.

    The adapter requests zero data access and performs topology/property queries
    only. Every opened handle is closed immediately after the bounded query.
    """

    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    STORAGE_DEVICE_PROPERTY = 0
    PROPERTY_STANDARD_QUERY = 0
    MAX_DESCRIPTOR_BYTES = 1024 * 1024

    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise OSError("Win32 volume topology is available only on Windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        self.kernel32.GetVolumePathNameW.restype = wintypes.BOOL
        self.kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetDriveTypeW.restype = wintypes.UINT
        self.kernel32.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        self.kernel32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.DeviceIoControl.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self.kernel32.DeviceIoControl.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    @staticmethod
    def _last_error(prefix: str) -> OSError:
        code = ctypes.get_last_error()
        return OSError(code, f"{prefix} failed with Win32 error {code}")

    def _open_device(self, path: str):
        handle = self.kernel32.CreateFileW(
            path,
            0,
            self.FILE_SHARE_READ | self.FILE_SHARE_WRITE | self.FILE_SHARE_DELETE,
            None,
            self.OPEN_EXISTING,
            0,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            raise self._last_error(f"CreateFileW({path})")
        return handle

    def get_volume_path_name(self, path: str) -> str:
        buf = ctypes.create_unicode_buffer(32768)
        if not self.kernel32.GetVolumePathNameW(str(path), buf, len(buf)):
            raise self._last_error("GetVolumePathNameW")
        return buf.value

    def get_drive_type(self, volume_mount_point: str) -> int:
        return int(self.kernel32.GetDriveTypeW(volume_mount_point))

    def get_volume_name(self, volume_mount_point: str) -> str:
        buf = ctypes.create_unicode_buffer(1024)
        if not self.kernel32.GetVolumeNameForVolumeMountPointW(volume_mount_point, buf, len(buf)):
            raise self._last_error("GetVolumeNameForVolumeMountPointW")
        return buf.value

    def get_disk_extents(self, volume_guid_path: str) -> tuple[DiskExtent, ...]:
        handle = self._open_device(volume_guid_path.rstrip("\\"))
        try:
            size = ctypes.sizeof(_VOLUME_DISK_EXTENTS_ONE)
            for _ in range(4):
                buf = ctypes.create_string_buffer(size)
                returned = wintypes.DWORD(0)
                ctypes.set_last_error(0)
                ok = self.kernel32.DeviceIoControl(
                    handle,
                    IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
                    None,
                    0,
                    buf,
                    size,
                    ctypes.byref(returned),
                    None,
                )
                if ok:
                    usable = max(int(returned.value), _EXTENTS_OFFSET)
                    return _parse_disk_extents_buffer(bytes(buf.raw[: max(usable, size)]))
                code = ctypes.get_last_error()
                if code != ERROR_MORE_DATA:
                    raise OSError(code, f"IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS failed with Win32 error {code}")
                count = int.from_bytes(buf.raw[:4], "little", signed=False)
                if count <= 1:
                    raise OSError(code, "IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS requested more data without a usable extent count")
                size = _EXTENTS_OFFSET + count * _EXTENT_SIZE
            raise OSError(ERROR_MORE_DATA, "IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS did not converge after bounded retries")
        finally:
            self.kernel32.CloseHandle(handle)

    def get_disk_identity(self, disk_number: int) -> PhysicalDiskIdentity:
        disk_number = int(disk_number)
        handle = self._open_device(rf"\\.\PhysicalDrive{disk_number}")
        query = _STORAGE_PROPERTY_QUERY()
        query.PropertyId = self.STORAGE_DEVICE_PROPERTY
        query.QueryType = self.PROPERTY_STANDARD_QUERY
        try:
            header = _STORAGE_DESCRIPTOR_HEADER()
            returned = wintypes.DWORD(0)
            ctypes.set_last_error(0)
            ok = self.kernel32.DeviceIoControl(
                handle,
                IOCTL_STORAGE_QUERY_PROPERTY,
                ctypes.byref(query),
                ctypes.sizeof(query),
                ctypes.byref(header),
                ctypes.sizeof(header),
                ctypes.byref(returned),
                None,
            )
            if not ok:
                raise self._last_error("IOCTL_STORAGE_QUERY_PROPERTY(header)")
            size = int(header.Size)
            if size < _DEVICE_DESCRIPTOR_MIN_SIZE or size > self.MAX_DESCRIPTOR_BYTES:
                raise ValueError(f"storage device descriptor size {size} is outside the accepted bounds")
            buf = ctypes.create_string_buffer(size)
            returned = wintypes.DWORD(0)
            ctypes.set_last_error(0)
            ok = self.kernel32.DeviceIoControl(
                handle,
                IOCTL_STORAGE_QUERY_PROPERTY,
                ctypes.byref(query),
                ctypes.sizeof(query),
                buf,
                size,
                ctypes.byref(returned),
                None,
            )
            if not ok:
                raise self._last_error("IOCTL_STORAGE_QUERY_PROPERTY(device)")
            used = int(returned.value)
            if used < _DEVICE_DESCRIPTOR_MIN_SIZE:
                raise ValueError(f"storage device descriptor returned only {used} bytes")
            return _parse_storage_device_descriptor(bytes(buf.raw[:used]), disk_number)
        finally:
            self.kernel32.CloseHandle(handle)


def probe_windows_volume(path: Path, *, api: WindowsVolumeAPI | None = None) -> WindowsVolumeTopology:
    """Return fail-closed Windows volume, disk-extent and device-identity evidence.

    SMB/network volumes are identified but are not queried with local-volume
    management IOCTLs because Microsoft documents that lane as unsupported.
    Disk-number evidence is retained even if the underlying device descriptor is
    virtual/ambiguous; only direct local buses with usable serials are eligible
    for automatic physical-media proof.
    """
    p = Path(path)
    if api is None:
        if platform.system() != "Windows":
            return WindowsVolumeTopology(path=str(p), error="not running on Windows")
        try:
            api = CtypesWindowsVolumeAPI()
        except Exception as exc:
            return WindowsVolumeTopology(path=str(p), error=f"Win32 topology adapter unavailable: {exc}")
    try:
        mount = api.get_volume_path_name(str(p))
        drive_type = int(api.get_drive_type(mount))
        drive_name = _DRIVE_TYPE_NAMES.get(drive_type, f"unknown_{drive_type}")
        if drive_type == DRIVE_REMOTE:
            return WindowsVolumeTopology(
                path=str(p),
                volume_mount_point=mount,
                drive_type=drive_type,
                drive_type_name=drive_name,
                local_volume=False,
                topology_supported=False,
                error="remote/network volume: Windows volume-management disk extents are not supported",
            )
        if drive_type not in {DRIVE_FIXED, DRIVE_REMOVABLE}:
            return WindowsVolumeTopology(
                path=str(p),
                volume_mount_point=mount,
                drive_type=drive_type,
                drive_type_name=drive_name,
                local_volume=False,
                topology_supported=False,
                error=f"drive type {drive_name} is not qualified for physical-backup proof",
            )
        volume_guid = api.get_volume_name(mount)
        extents = tuple(api.get_disk_extents(volume_guid))
        disk_numbers = tuple(sorted({int(e.disk_number) for e in extents}))
        if not disk_numbers:
            raise ValueError("Windows volume topology returned no disk numbers")
        identities: list[PhysicalDiskIdentity] = []
        for number in disk_numbers:
            try:
                identity = api.get_disk_identity(number)
                if int(identity.disk_number) != number:
                    raise ValueError(
                        f"physical-disk descriptor number mismatch: expected {number}, got {identity.disk_number}"
                    )
            except Exception as exc:
                identity = PhysicalDiskIdentity(
                    disk_number=number,
                    error=f"physical-disk descriptor query failed closed: {exc}",
                )
            identities.append(identity)
        serial_keys = [i.serial_number.casefold() for i in identities if i.serial_number]
        duplicate_serials = len(serial_keys) != len(set(serial_keys))
        hardware_ok = (
            bool(identities)
            and all(i.hardware_identity_eligible for i in identities)
            and not duplicate_serials
        )
        hardware_errors = [i.error for i in identities if not i.hardware_identity_eligible and i.error]
        if duplicate_serials:
            hardware_errors.append("multiple disk numbers report the same storage-device serial")
        return WindowsVolumeTopology(
            path=str(p),
            volume_mount_point=mount,
            volume_guid_path=volume_guid,
            drive_type=drive_type,
            drive_type_name=drive_name,
            local_volume=True,
            topology_supported=True,
            disk_numbers=disk_numbers,
            disk_extents=extents,
            disk_identities=tuple(identities),
            hardware_identity_supported=hardware_ok,
            hardware_identity_error="; ".join(hardware_errors) if hardware_errors else None,
        )
    except Exception as exc:
        return WindowsVolumeTopology(path=str(p), error=f"Windows volume topology probe failed closed: {exc}")
