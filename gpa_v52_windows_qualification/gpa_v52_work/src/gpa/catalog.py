from __future__ import annotations
import calendar, json, math, sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA='''
CREATE TABLE IF NOT EXISTS assets(
 id TEXT PRIMARY KEY, path TEXT NOT NULL, capture_start TEXT, capture_end TEXT,
 year INTEGER, month INTEGER, lat REAL, lon REAL, description TEXT, place TEXT,
 needs_review INTEGER NOT NULL DEFAULT 0, identity_review INTEGER NOT NULL DEFAULT 0,
 logical_item_count INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS tags(asset_id TEXT, kind TEXT, value TEXT);
CREATE INDEX IF NOT EXISTS idx_assets_ym ON assets(year,month);
CREATE INDEX IF NOT EXISTS idx_assets_capture ON assets(capture_start,capture_end);
CREATE INDEX IF NOT EXISTS idx_tags_value ON tags(kind,value);
'''
class Catalog:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path);self.db.executescript(SCHEMA)
    def add(self,**x):
        cols=['id','path','capture_start','capture_end','year','month','lat','lon','description','place','needs_review','identity_review','logical_item_count']
        self.db.execute(f"INSERT OR REPLACE INTO assets({','.join(cols)}) VALUES({','.join('?'*len(cols))})",[x.get(c) for c in cols]);self.db.commit()
    def tag(self,asset_id,kind,value):self.db.execute('INSERT INTO tags VALUES(?,?,?)',(asset_id,kind,value));self.db.commit()
    def by_month(self,y,m):return self.db.execute('SELECT id,path FROM assets WHERE year=? AND month=?',(y,m)).fetchall()
    def tagged(self,kind,value):return self.db.execute('SELECT DISTINCT a.id,a.path FROM assets a JOIN tags t ON a.id=t.asset_id WHERE t.kind=? AND t.value=?',(kind,value)).fetchall()
    def by_place(self,value):return self.db.execute('SELECT id,path FROM assets WHERE place=? ORDER BY path',(value,)).fetchall()
    def needs_review(self):return self.db.execute('SELECT id,path FROM assets WHERE needs_review=1 ORDER BY path').fetchall()
    def identity_review(self):return self.db.execute('SELECT id,path FROM assets WHERE identity_review=1 ORDER BY path').fetchall()
    def between(self,start_iso:str,end_iso:str):
        # Interval overlap, so reduced-precision dates remain searchable without fake exact timestamps.
        return self.db.execute("SELECT id,path FROM assets WHERE capture_start IS NOT NULL AND capture_end IS NOT NULL AND capture_start<=? AND capture_end>=? ORDER BY capture_start,path",(end_iso,start_iso)).fetchall()
    def search_text(self,text:str):
        q='%'+text.casefold()+'%'
        return self.db.execute('''SELECT DISTINCT a.id,a.path FROM assets a LEFT JOIN tags t ON a.id=t.asset_id
            WHERE lower(COALESCE(a.description,'')) LIKE ? OR lower(COALESCE(a.place,'')) LIKE ? OR lower(COALESCE(t.value,'')) LIKE ? ORDER BY a.path''',(q,q,q)).fetchall()
    def near(self,lat,lon,km):
        rows=self.db.execute('SELECT id,path,lat,lon FROM assets WHERE lat IS NOT NULL AND lon IS NOT NULL').fetchall();out=[]
        for i,p,la,lo in rows:
            r=6371.0088; p1=math.radians(lat);p2=math.radians(la);dp=p2-p1;dl=math.radians(lo-lon)
            a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
            d=2*r*math.asin(math.sqrt(a))
            if d<=km:out.append((i,p,d))
        return sorted(out,key=lambda x:x[2])
    def close(self):self.db.close()

def date_interval(record:dict|None)->tuple[str|None,str|None,int|None,int|None]:
    if not record:return None,None,None,None
    precision=record.get('precision');value=record.get('value');year=record.get('year');month=record.get('month')
    if precision=='instant' and value:return value,value,year,month
    if precision=='month' and year and month:
        last=calendar.monthrange(int(year),int(month))[1]
        return f'{year:04d}-{month:02d}-01T00:00:00',f'{year:04d}-{month:02d}-{last:02d}T23:59:59.999999',int(year),int(month)
    if precision=='year' and year:
        return f'{year:04d}-01-01T00:00:00',f'{year:04d}-12-31T23:59:59.999999',int(year),None
    if precision=='range' and record.get('start') and record.get('end'):
        return record['start'],record['end'],None,None
    return None,None,year,month

def rebuild_catalog_from_archive(root:Path,db_path:Path)->Catalog:
    from .identity_observations import IdentityObservationStore
    root=Path(root);db_path=Path(db_path)
    if db_path.exists():db_path.unlink()
    c=Catalog(db_path);identity_store=IdentityObservationStore(root);assets_dir=root/'metadata'/'assets'
    if not assets_dir.exists():return c
    for d in sorted(p for p in assets_dir.iterdir() if p.is_dir()):
        cur=d/'current.json'
        if not cur.exists():continue
        try:
            rid=json.loads(cur.read_text(encoding='utf-8'))['revision_id']
            rec=json.loads((d/'revisions'/f'{rid}.json').read_text(encoding='utf-8'))
        except Exception:continue
        date=rec.get('date');start,end,year,month=date_interval(date)
        loc=rec.get('location') or {};projection=rec.get('projection') or {};path=projection.get('media')
        if not path:continue
        raws=rec.get('google_raw') or []
        descriptions={r.get('description') for r in raws if isinstance(r,dict) and r.get('description')}
        desc=next(iter(descriptions)) if len(descriptions)==1 else None
        chronology_review=int((date or {}).get('confidence') in {'unknown','conflict','probable'} or (date or {}).get('precision') in {'unknown','range','year'})
        identity=identity_store.summary(rec.get('asset_id',d.name))
        identity_review=int(identity.needs_identity_review)
        needs=int(bool(chronology_review or identity_review))
        place=rec.get('place') or {};place_label=rec.get('user_location_label') or place.get('label')
        c.add(id=rec.get('asset_id',d.name),path=path,capture_start=start,capture_end=end,year=year,month=month,
              lat=loc.get('lat'),lon=loc.get('lon'),description=desc,place=place_label,needs_review=needs,
              identity_review=identity_review,logical_item_count=identity.possible_logical_item_count)
        aid=rec.get('asset_id',d.name)
        if rec.get('user_location_label'):c.tag(aid,'user_place',rec['user_location_label'])
        if place.get('name'):c.tag(aid,'place',place['name'])
        if place.get('admin1_name'):c.tag(aid,'admin1',place['admin1_name'])
        if place.get('country_name'):c.tag(aid,'country',place['country_name'])
        elif place.get('country_code'):c.tag(aid,'country_code',place['country_code'])
        for album in rec.get('albums') or []:
            if album.get('title'):c.tag(aid,'album',album['title'])
        people=set()
        for r in raws:
            if isinstance(r,dict):
                for p in r.get('people') or []:
                    if isinstance(p,dict) and p.get('name'):people.add(str(p['name']))
        for p in sorted(people):c.tag(aid,'person',p)
    return c
