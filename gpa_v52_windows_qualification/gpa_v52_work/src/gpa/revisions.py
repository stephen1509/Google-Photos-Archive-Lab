from __future__ import annotations
import hashlib, json, os
from pathlib import Path
from .transactions import write_json_new, fsync_dir, stage_bytes

class RevisionError(Exception):pass

class RevisionStore:
    def __init__(self,root:Path):self.root=Path(root)
    def _asset(self,asset_id):return self.root/'metadata'/'assets'/asset_id

    @staticmethod
    def _canonical(record:dict)->bytes:
        return json.dumps(record,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()

    @classmethod
    def revision_id(cls,record:dict)->str:
        """Return the immutable content-derived revision id without writing anything."""
        return hashlib.sha256(cls._canonical(record)).hexdigest()[:16]

    def prepare_revision(self,asset_id:str,record:dict)->tuple[Path,str]:
        """Write an immutable revision, but do not change the current pointer.

        This split is important for crash-safe projection updates: the intended new
        metadata state can be durably recorded before any current media/XMP paths
        move, while the old current pointer remains authoritative until commit.
        """
        base=self._asset(asset_id)/'revisions';base.mkdir(parents=True,exist_ok=True)
        canonical=self._canonical(record);rid=self.revision_id(record)
        path=base/f'{rid}.json'
        if not path.exists():write_json_new(path,{'revision_id':rid,**record})
        return path,rid

    def add_revision(self,asset_id:str,record:dict)->Path:
        path,rid=self.prepare_revision(asset_id,record)
        self.set_current(asset_id,rid)
        return path

    def set_current(self,asset_id,rid):
        d=self._asset(asset_id);d.mkdir(parents=True,exist_ok=True)
        rev=d/'revisions'/f'{rid}.json'
        if not rev.exists():raise RevisionError(f'cannot point at missing revision: {rid}')
        target=d/'current.json'
        payload=(json.dumps({'revision_id':rid},sort_keys=True)+'\n').encode()
        staged=stage_bytes(payload,d)
        try:
            os.replace(staged,target)
            fsync_dir(d)
        finally:
            staged.unlink(missing_ok=True)

    def revisions(self,asset_id):
        d=self._asset(asset_id)/'revisions'
        return sorted(d.glob('*.json')) if d.exists() else []

    def current(self,asset_id):
        p=self._asset(asset_id)/'current.json'
        return json.loads(p.read_text())['revision_id'] if p.exists() else None

    def revision_record(self,asset_id,rid)->dict|None:
        p=self._asset(asset_id)/'revisions'/f'{rid}.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None

    def current_record(self,asset_id)->dict|None:
        rid=self.current(asset_id)
        return self.revision_record(asset_id,rid) if rid else None

    def archive_blob(self,asset_id:str,kind:str,data:bytes,suffix:str='') -> Path:
        """Preserve an exact prior projection sidecar/blob by content hash.

        Mutable *current projection* files may legitimately evolve after a later
        Takeout corrects metadata. Before replacement/removal we keep the exact old
        bytes here, so history does not depend on reconstructing an earlier XMP.
        """
        digest=hashlib.sha256(data).hexdigest()
        safe_kind=''.join(c if c.isalnum() or c in '-_' else '_' for c in kind) or 'blob'
        d=self._asset(asset_id)/'blobs'/safe_kind;d.mkdir(parents=True,exist_ok=True)
        p=d/f'{digest}{suffix}'
        if not p.exists():
            fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o666)
            try:
                with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
                fsync_dir(d)
            except Exception:
                p.unlink(missing_ok=True);fsync_dir(d);raise
        return p
