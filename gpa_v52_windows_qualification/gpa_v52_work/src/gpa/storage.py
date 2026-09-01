from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path, PurePath
import os, shutil, subprocess
from .output import utf16_units, RESERVED

def _existing_ancestor(path:Path)->Path:
    p=Path(path)
    while not p.exists() and p.parent!=p:p=p.parent
    return p

def detect_filesystem_type(path:Path)->str|None:
    """Best-effort, read-only filesystem type detection for destination preflight."""
    p=_existing_ancestor(path)
    if os.name=='nt':
        try:
            import ctypes
            from ctypes import wintypes
            volume=ctypes.create_unicode_buffer(32768);fs=ctypes.create_unicode_buffer(256)
            if not ctypes.windll.kernel32.GetVolumePathNameW(str(p),volume,len(volume)):return None
            serial=wintypes.DWORD();max_component=wintypes.DWORD();flags=wintypes.DWORD()
            ok=ctypes.windll.kernel32.GetVolumeInformationW(volume.value,None,0,ctypes.byref(serial),ctypes.byref(max_component),ctypes.byref(flags),fs,len(fs))
            return fs.value.lower() if ok and fs.value else None
        except Exception:return None
    try:
        r=subprocess.run(['findmnt','-n','-o','FSTYPE','--target',str(p)],capture_output=True,text=True,timeout=2,check=False)
        if r.returncode==0 and r.stdout.strip():return r.stdout.strip().splitlines()[0].strip().lower()
    except Exception:pass
    # Portable-ish Linux fallback: choose the longest mounted prefix.
    try:
        target=str(p.resolve());best=('',None)
        for table in ('/proc/self/mounts','/proc/mounts'):
            if not Path(table).exists():continue
            for line in Path(table).read_text(errors='replace').splitlines():
                parts=line.split()
                if len(parts)<3:continue
                mount=parts[1].replace('\\040',' ');fstype=parts[2]
                try:
                    mr=str(Path(mount).resolve())
                    if target==mr or target.startswith(mr.rstrip('/')+'/'):
                        if len(mr)>len(best[0]):best=(mr,fstype.lower())
                except Exception:continue
            if best[1]:return best[1]
    except Exception:pass
    return None

@dataclass
class StoragePreflight:
    ok:bool
    errors:list[str]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)
    required_bytes:int=0
    free_bytes:int|None=None

def preflight_destination(root:Path, planned_sizes:list[int], fs_type:str|None=None, staging_largest:bool=True, reserve_fraction:float=0.05, metadata_overhead_bytes:int=0)->StoragePreflight:
    errs=[];warn=[];sizes=[max(0,int(x)) for x in planned_sizes]
    final=sum(sizes);staging=max(sizes,default=0) if staging_largest else 0
    required=final+staging+max(0,int(metadata_overhead_bytes))
    if fs_type and fs_type.lower() in {'fat32','vfat'} and any(x>4*1024**3-1 for x in sizes):
        errs.append('FAT32 cannot store one or more planned files larger than 4 GiB')
    try:free=shutil.disk_usage(_existing_ancestor(root)).free
    except OSError:free=None;warn.append('could not determine free space')
    if free is not None:
        reserve=int(free*reserve_fraction)
        if required>free-reserve:errs.append('insufficient free space including staging, metadata overhead, and reserve')
    return StoragePreflight(not errs,errs,warn,required,free)

def windows_path_risk(relative_path:str,conservative_limit:int=240,component_limit:int=255)->list[str]:
    problems=[];parts=[p for p in relative_path.replace('\\','/').split('/') if p]
    if any(utf16_units(p)>component_limit for p in parts):problems.append('component exceeds 255 UTF-16 code units')
    if utf16_units(relative_path)>conservative_limit:problems.append('path exceeds conservative Windows UTF-16 interoperability limit')
    for p in parts:
        stem=PurePath(p.rstrip(' .')).stem.upper()
        if stem in RESERVED:problems.append(f'Windows reserved device component: {p}')
        if p.endswith((' ','.')):problems.append(f'Windows trailing space/dot component: {p}')
    return problems

@dataclass(frozen=True)
class StorageOperation:
    """Space-impact estimate for one sequential archive operation.

    ``permanent_growth`` is space expected to remain after the operation.
    ``transient_growth`` is additional temporary/coexisting space above the
    pre-operation baseline (for example old+new media during a relocation).
    ``written_file_sizes`` contains individual files that must be newly written
    or staged so filesystem per-file limits (notably FAT32) can be checked.
    """
    asset_id:str
    action:str
    permanent_growth:int=0
    transient_growth:int=0
    written_file_sizes:tuple[int,...]=()

@dataclass
class IncrementalStoragePreflight(StoragePreflight):
    permanent_growth_bytes:int=0
    transient_peak_bytes:int=0
    operations:dict[str,int]=field(default_factory=dict)
    filesystem_type:str|None=None


def preflight_operations(root:Path,operations:list[StorageOperation],*,fs_type:str|None=None,reserve_fraction:float=0.05,global_metadata_reserve:int=1024*1024)->IncrementalStoragePreflight:
    """Preflight a sequential migration/update using *delta* space accounting.

    The final requirement is the sum of all permanent growth plus the largest
    single-operation transient peak. This is deliberately conservative for the
    executor's sequential transaction model while avoiding the incorrect
    assumption that an incremental import rewrites the whole existing library.
    """
    errors=[];warnings=[];ops=list(operations);effective_fs=(fs_type.lower() if fs_type else detect_filesystem_type(root))
    permanent=sum(max(0,int(o.permanent_growth)) for o in ops)
    transient=max((max(0,int(o.transient_growth)) for o in ops),default=0)
    reserve=max(0,int(global_metadata_reserve))
    required=permanent+transient+reserve
    counts={}
    for o in ops:counts[o.action]=counts.get(o.action,0)+1
    if effective_fs in {'fat32','vfat'}:
        limit=4*1024**3-1
        if any(sz>limit for o in ops for sz in o.written_file_sizes):
            errors.append('FAT32 cannot store or stage one or more files larger than 4 GiB')
    target=_existing_ancestor(root)
    try:free=shutil.disk_usage(target).free
    except OSError:free=None;warnings.append('could not determine free space')
    if free is not None:
        reserve_free=int(free*reserve_fraction)
        if required>free-reserve_free:
            errors.append('insufficient free space for incremental permanent growth, transient transaction peak, metadata reserve, and safety reserve')
    if effective_fs is None:warnings.append('could not determine destination filesystem type; per-filesystem limits cannot be fully preflighted')
    return IncrementalStoragePreflight(not errors,errors,warnings,required,free,permanent,transient,counts,effective_fs)
