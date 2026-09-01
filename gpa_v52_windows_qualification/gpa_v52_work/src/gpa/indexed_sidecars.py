from __future__ import annotations
from collections import defaultdict
from .zipindex import Member, ZipLibrary, SourceProblem
from .sidecars import expected_sidecar_names, parse_google_sidecar, _is_album_metadata_object, _score_title

class SidecarIndex:
    """O(1)-ish deterministic lookup using Google's observed filename generation rules."""
    def __init__(self,members:list[Member],lib:ZipLibrary,tolerant:bool=False):
        self.lib=lib;self.json_members=[m for m in members if m.suffix=='.json'];self.problems:list[SourceProblem]=[]
        if tolerant:
            self.parsed,self.problems=lib.batch_json_tolerant(self.json_members) if self.json_members else ({},[])
        else:self.parsed=lib.batch_json(self.json_members) if self.json_members else {}
        self.by_name=defaultdict(list)
        for m in self.json_members:
            d=self.parsed.get((m.archive,m.path))
            if isinstance(d,dict) and not _is_album_metadata_object(d):self.by_name[(m.logical_dir,m.basename)].append(m)
    def _candidates(self,media:Member):
        candidates=[]
        for name in expected_sidecar_names(media.basename):
            for s in self.by_name.get((media.logical_dir,name),[]):
                sc=parse_google_sidecar(self.parsed[(s.archive,s.path)])
                candidates.append((s,sc,100+_score_title(media.basename,sc)))
        candidates.sort(key=lambda x:x[2],reverse=True)
        return candidates

    def match(self,media:Member):
        candidates=self._candidates(media)
        if not candidates or candidates[0][2]<=80:return None
        top=[x for x in candidates if x[2]==candidates[0][2]]
        if len(top)>1:
            canon={__import__('json').dumps(x[1].raw,sort_keys=True,separators=(',',':'),ensure_ascii=False) for x in top}
            if len(canon)!=1:return None
        return [x[0] for x in top],top[0][1]

    def choose(self,media:Member):
        got=self.match(media)
        if not got:return None
        members,sc=got
        return members[0],sc
