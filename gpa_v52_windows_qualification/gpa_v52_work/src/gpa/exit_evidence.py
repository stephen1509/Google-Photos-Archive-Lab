from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
import json

_SCHEMA='gpa.google-photos-exit-evidence.v1'

@dataclass
class GooglePhotosExitEvidence:
    schema:str=_SCHEMA
    captured_at:str=''
    shared_albums_reviewed:bool=False
    wanted_shared_media_secured:bool=False
    partner_sharing_reviewed:bool=False
    locked_folder_reviewed:bool=False
    takeout_scope_reviewed:bool=False
    recent_changes_accounted_for:bool=False
    notes:list[str]=field(default_factory=list)

    @property
    def complete(self)->bool:
        return all((self.shared_albums_reviewed,self.wanted_shared_media_secured,self.partner_sharing_reviewed,self.locked_folder_reviewed,self.takeout_scope_reviewed,self.recent_changes_accounted_for))
    def to_dict(self)->dict:return asdict(self)


def create_exit_evidence(**kwargs)->GooglePhotosExitEvidence:
    e=GooglePhotosExitEvidence(captured_at=datetime.now(timezone.utc).isoformat())
    for k,v in kwargs.items():
        if not hasattr(e,k):raise TypeError(f'unknown exit-evidence field: {k}')
        setattr(e,k,v)
    return e


def write_exit_evidence(path:Path,evidence:GooglePhotosExitEvidence)->Path:
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(evidence.to_dict(),ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(path);return path


def load_exit_evidence(path:Path)->GooglePhotosExitEvidence:
    d=json.loads(Path(path).read_text(encoding='utf-8'))
    if d.get('schema')!=_SCHEMA:raise ValueError(f'unsupported exit-evidence schema: {d.get("schema")}')
    fields={k:v for k,v in d.items() if k in GooglePhotosExitEvidence.__dataclass_fields__}
    return GooglePhotosExitEvidence(**fields)


def verify_exit_evidence(path:Path|None)->tuple[bool,list[str]]:
    if path is None:return False,['No durable Google Photos exit checklist was supplied.']
    try:e=load_exit_evidence(path)
    except Exception as ex:return False,[f'Exit checklist could not be read: {ex}']
    missing=[]
    labels={
        'shared_albums_reviewed':'shared albums reviewed',
        'wanted_shared_media_secured':'wanted shared media saved/exported independently',
        'partner_sharing_reviewed':'partner sharing reviewed',
        'locked_folder_reviewed':'Locked Folder reviewed',
        'takeout_scope_reviewed':'Google Photos Takeout scope/albums reviewed',
        'recent_changes_accounted_for':'changes made during Takeout creation accounted for by freeze/follow-up export',
    }
    for field,label in labels.items():
        if not getattr(e,field):missing.append(label)
    return not missing,[f'Exit checklist incomplete: {x}' for x in missing]
