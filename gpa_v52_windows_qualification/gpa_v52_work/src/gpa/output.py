from __future__ import annotations
import hashlib
import re
import unicodedata
from pathlib import PurePath
from .model import DateFact, DatePrecision

INVALID=re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows also treats superscript 1/2/3 as digits in COM/LPT device names.
_DEVICE_DIGITS='123¹²³'
RESERVED={"CON","PRN","AUX","NUL",*(f"COM{i}" for i in _DEVICE_DIGITS),*(f"LPT{i}" for i in _DEVICE_DIGITS)}
MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December']

def utf16_units(s:str)->int:
    """Windows path component limits are measured in UTF-16 code units, not Python code points."""
    return len(s.encode('utf-16-le'))//2

def _trim_utf16(s:str,max_units:int)->str:
    if max_units<=0:return ''
    out=[];used=0
    for ch in s:
        u=utf16_units(ch)
        if used+u>max_units:break
        out.append(ch);used+=u
    return ''.join(out)

def portable_filename(name:str,max_component:int=180)->str:
    # NFC is chosen for stable output; original spelling/codepoints remain in provenance.
    s=unicodedata.normalize('NFC',INVALID.sub('_',name)).rstrip(' .') or '_'
    p=PurePath(s);stem=p.stem
    if stem.upper() in RESERVED:s='_'+s;p=PurePath(s)
    if utf16_units(s)>max_component:
        digest=hashlib.sha256(s.encode('utf-8')).hexdigest()[:10]
        suffix=p.suffix
        fixed=utf16_units('__'+digest+suffix)
        budget=max_component-fixed
        stem=_trim_utf16(p.stem,max(1,budget))
        s=f"{stem}__{digest}{suffix}"
    return s

def windows_collision_key(relative_name:str)->str:
    """Approximate Windows case-insensitive/NFC namespace collision semantics."""
    return unicodedata.normalize('NFC',relative_name).casefold()

def with_hash_suffix(name:str,sha256:str,max_component:int=180,hash_len:int=10)->str:
    p=PurePath(name);suffix=p.suffix;token='__'+sha256[:hash_len]
    budget=max_component-utf16_units(token+suffix)
    stem=_trim_utf16(p.stem,max(1,budget))
    return portable_filename(f'{stem}{token}{suffix}',max_component=max_component)

def resolve_filename_collisions(items:list[tuple[str,str,str]])->dict[str,str]:
    """Resolve planned output collisions deterministically.

    items: (asset_id, output_dir, proposed_name).  Returns asset_id -> final name.
    Every member of a collision set receives a stable hash-like suffix derived from asset_id,
    avoiding order-dependent "first file wins" behavior.
    """
    buckets={}
    for asset_id,outdir,name in items:
        key=(windows_collision_key(outdir),windows_collision_key(name))
        buckets.setdefault(key,[]).append((asset_id,name))
    out={}
    for rows in buckets.values():
        if len(rows)==1:
            asset_id,name=rows[0];out[asset_id]=name;continue
        for asset_id,name in rows:
            # asset_id is SHA-derived in the planner; hash it again only to keep this helper generic.
            digest=hashlib.sha256(asset_id.encode('utf-8')).hexdigest()
            out[asset_id]=with_hash_suffix(name,digest)
    return out

def placement_dir(fact:DateFact)->str:
    if fact.year and fact.month and fact.precision in {DatePrecision.INSTANT,DatePrecision.DAY,DatePrecision.MONTH}:
        return f"Photos/{fact.year:04d}/{fact.month:02d} - {MONTHS[fact.month-1]}"
    if fact.precision==DatePrecision.RANGE and fact.start and fact.end:
        return f"_Needs Placement/Date/{fact.start.date().isoformat()}_to_{fact.end.date().isoformat()}"
    if fact.year and fact.precision==DatePrecision.YEAR:
        return f"_Needs Placement/Date/{fact.year:04d}-year-only"
    return "_Needs Placement/Date/Unknown"
