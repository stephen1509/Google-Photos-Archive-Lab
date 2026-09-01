from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

STATES={'DISCOVERED','STAGED','VERIFIED','COMMITTED','FAILED'}
class Ledger:
    def __init__(self,path:Path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs(asset_id TEXT PRIMARY KEY,state TEXT NOT NULL,staged_path TEXT,dest_path TEXT,error TEXT,updated TEXT NOT NULL)')
        self.db.commit()
    def set(self,asset_id,state,staged_path=None,dest_path=None,error=None):
        if state not in STATES:raise ValueError(state)
        now=datetime.now(timezone.utc).isoformat()
        self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET state=excluded.state,staged_path=excluded.staged_path,dest_path=excluded.dest_path,error=excluded.error,updated=excluded.updated',(asset_id,state,str(staged_path) if staged_path else None,str(dest_path) if dest_path else None,error,now));self.db.commit()
    def get(self,asset_id):
        r=self.db.execute('SELECT state,staged_path,dest_path,error FROM jobs WHERE asset_id=?',(asset_id,)).fetchone()
        return None if not r else {'state':r[0],'staged_path':r[1],'dest_path':r[2],'error':r[3]}
    def resumable(self):
        return self.db.execute("SELECT asset_id,state,staged_path,dest_path FROM jobs WHERE state!='COMMITTED' ORDER BY asset_id").fetchall()

def classify_recovery(row:dict)->str:
    from pathlib import Path
    state=row['state'];s=Path(row['staged_path']) if row.get('staged_path') else None;d=Path(row['dest_path']) if row.get('dest_path') else None
    if state=='COMMITTED':return 'done'
    if d and d.exists():return 'verify_destination_before_marking_committed'
    if s and s.exists():return 'resume_from_staged'
    return 'restart_asset'
