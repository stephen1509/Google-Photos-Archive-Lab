from __future__ import annotations
import re
from pathlib import PurePosixPath
from .zipindex import Member, ZipLibrary
from .model import GoogleSidecar
from .formats import MEDIA_SUFFIXES
DUP_MEDIA=re.compile(r'^(.+)\((\d+)\)(\.[^.]+)$')
DUP_END=re.compile(r"\((\d+)\)(?=\.[^.]+$)")

def fit_google_sidecar_base(base:str)->str:
    full=base+'.json'
    if len(full)<=51:return full
    return base[:51-len('.json')]+'.json'

def expected_sidecar_names(media_name:str)->list[str]:
    """Observed Google Takeout naming families, deterministic before any heuristic review."""
    out=[]
    def add(x):
        if x not in out:out.append(x)
    add(fit_google_sidecar_base(media_name))
    add(fit_google_sidecar_base(media_name+'.supplemental-metadata'))
    m=DUP_MEDIA.match(media_name)
    if m:
        bare=m.group(1)+m.group(3);num=m.group(2)
        fullbase=bare+'.supplemental-metadata'
        truncbase=fullbase if len(fullbase+'.json')<=51 else fullbase[:51-len('.json')]
        add(f'{truncbase}({num}).json')
        add(f'{bare}({num}).json')
    return out

def _is_album_metadata_object(d:dict)->bool:
    return ('albumData' in d) or ('access' in d and 'title' in d and 'photoTakenTime' not in d) or ('date' in d and 'photoTakenTime' not in d and 'geoData' not in d and 'geoInfo' not in d)

def parse_google_sidecar(d:dict)->GoogleSidecar:
    def epoch(key):
        v=d.get(key) or {}
        try:return int(v.get('timestamp'))
        except (TypeError,ValueError,AttributeError):return None
    def geo(*keys):
        for k in keys:
            g=d.get(k)
            if isinstance(g,dict):
                try:lat=float(g.get('latitude'));lon=float(g.get('longitude'))
                except (TypeError,ValueError):continue
                if lat==0 and lon==0:continue
                if -90<=lat<=90 and -180<=lon<=180:return (lat,lon)
        return None
    people=[]
    for p in d.get('people') or []:
        if isinstance(p,dict) and p.get('name'):people.append(str(p['name']))
    return GoogleSidecar(title=d.get('title'),taken_epoch=epoch('photoTakenTime'),creation_epoch=epoch('creationTime'),description=d.get('description') or None,geo_current=geo('geoData','geoInfo'),geo_exif=geo('geoDataExif','geoInfoExif'),favorited=d.get('favorited') if isinstance(d.get('favorited'),bool) else None,people=people,url=str(d.get('url')) if d.get('url') else None,origin=d.get('googlePhotosOrigin') if isinstance(d.get('googlePhotosOrigin'),dict) else {},raw=d)

def canonical_sidecar_targets(sidecar_name:str)->set[str]:
    # Compatibility helper only. Deterministic auto-matching is media->expected_sidecar_names().
    targets=set()
    if sidecar_name.lower().endswith('.json'):targets.add(sidecar_name[:-5])
    return targets

def _score_title(media_name:str,sc:GoogleSidecar)->int:
    if sc.title==media_name:return 30
    if sc.title:
        p=PurePosixPath(media_name);t=PurePosixPath(sc.title);bstem=re.sub(r'\(\d+\)$','',p.stem)
        if t.suffix.lower()==p.suffix.lower() and t.stem==bstem:return 12
        return -20
    return 0

def candidate_sidecars(media:Member,all_members:list[Member],lib:ZipLibrary)->list[tuple[Member,GoogleSidecar,int]]:
    expected=set(expected_sidecar_names(media.basename));out=[]
    for s in all_members:
        if s.suffix!='.json' or s.logical_dir!=media.logical_dir or s.basename not in expected:continue
        try:d=lib.json(s)
        except Exception:continue
        if _is_album_metadata_object(d):continue
        sc=parse_google_sidecar(d);out.append((s,sc,100+_score_title(media.basename,sc)))
    return sorted(out,key=lambda x:x[2],reverse=True)

def choose_sidecar(media:Member,all_members:list[Member],lib:ZipLibrary)->tuple[Member,GoogleSidecar]|None:
    cs=candidate_sidecars(media,all_members,lib)
    if not cs:return None
    if len(cs)>1 and cs[0][2]==cs[1][2]:return None
    if cs[0][2]<=80:return None
    return cs[0][0],cs[0][1]
