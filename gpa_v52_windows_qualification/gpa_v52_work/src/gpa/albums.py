from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
from pathlib import PurePosixPath
from .model import SourceRef
from .sidecars import _is_album_metadata_object
from .zipindex import Member

@dataclass
class AlbumRecord:
    album_id:str
    logical_dir:str
    title:str|None
    sources:list[SourceRef]=field(default_factory=list)
    raw_records:list[dict]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)

class AlbumIndex:
    """Detect album metadata by JSON content, never localized metadata filename."""
    def __init__(self,members:list[Member],parsed_json:dict[tuple[str,str],dict]):
        grouped={}
        for m in members:
            if m.suffix!='.json':continue
            d=parsed_json.get((m.archive,m.path))
            if not isinstance(d,dict) or not _is_album_metadata_object(d):continue
            grouped.setdefault(m.logical_dir,[]).append((m,d))
        self.by_dir={}
        for logical_dir,rows in grouped.items():
            titles={str(d.get('title')) for _,d in rows if d.get('title') not in (None,'')}
            title=next(iter(titles)) if len(titles)==1 else None
            warnings=[]
            if len(titles)>1:warnings.append(f'conflicting album titles: {sorted(titles)}')
            if not titles:warnings.append('untitled album preserved without inventing a title')
            aid=hashlib.sha256(logical_dir.encode('utf-8')).hexdigest()[:24]
            self.by_dir[logical_dir]=AlbumRecord(
                aid,logical_dir,title,
                [m.ref for m,_ in rows],
                [d for _,d in rows],warnings,
            )
    def album_for(self,member:Member)->AlbumRecord|None:
        return self.by_dir.get(member.logical_dir)
