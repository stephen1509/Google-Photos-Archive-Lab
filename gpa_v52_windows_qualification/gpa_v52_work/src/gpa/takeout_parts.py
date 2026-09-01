from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import re

_PART_RE=re.compile(r'^(?P<base>takeout(?:-[^-]+)*-)(?P<num>\d{3,})(?P<ext>\.(?:zip|tgz|tar\.gz))$',re.I)

@dataclass(frozen=True)
class TakeoutPartGroup:
    key:str
    present:tuple[int,...]
    missing_internal:tuple[int,...]=()
    expected_count:int|None=None
    missing_expected:tuple[int,...]=()
    duplicate_numbers:tuple[int,...]=()

    @property
    def has_known_gap(self)->bool:
        return bool(self.missing_internal or self.missing_expected or self.duplicate_numbers)

@dataclass
class TakeoutPartAnalysis:
    groups:list[TakeoutPartGroup]=field(default_factory=list)
    unmatched:list[str]=field(default_factory=list)
    @property
    def known_gaps(self)->int:return sum(len(g.missing_internal)+len(g.missing_expected) for g in self.groups)
    @property
    def ok(self)->bool:return all(not g.has_known_gap for g in self.groups)


def analyze_takeout_parts(paths,expected_counts:dict[str,int]|None=None)->TakeoutPartAnalysis:
    expected_counts=expected_counts or {};grouped={};unmatched=[]
    for raw in paths:
        name=Path(str(raw)).name;m=_PART_RE.match(name)
        if not m:unmatched.append(str(raw));continue
        key=(m.group('base')+m.group('ext')).casefold();num=int(m.group('num'))
        grouped.setdefault(key,[]).append(num)
    groups=[]
    for key,nums in sorted(grouped.items()):
        present=sorted(set(nums));dupes=sorted(n for n in set(nums) if nums.count(n)>1)
        max_present=max(present,default=0);internal=[n for n in range(1,max_present+1) if n not in present]
        expected=expected_counts.get(key)
        missing_expected=[]
        if expected is not None:
            if expected<0:raise ValueError('expected part count must be non-negative')
            missing_expected=[n for n in range(1,expected+1) if n not in present]
        groups.append(TakeoutPartGroup(key,tuple(present),tuple(internal),expected,tuple(missing_expected),tuple(dupes)))
    return TakeoutPartAnalysis(groups,unmatched)
