from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import math, sqlite3

@dataclass(frozen=True)
class PlaceEvidence:
    label:str
    name:str
    country_code:str|None
    admin1:str|None
    distance_km:float
    geoname_id:int|None
    dataset:str
    country_name:str|None=None
    admin1_name:str|None=None
    confidence:str='derived'
    note:str='Nearest-place label derived from exact GPS; GPS remains authoritative.'


def haversine_km(lat1:float,lon1:float,lat2:float,lon2:float)->float:
    r=6371.0088;p1=math.radians(lat1);p2=math.radians(lat2);dp=p2-p1;dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1.0,math.sqrt(a)))


class GeoNamesGazetteer:
    """Small offline reverse-place index built from GeoNames-style rows.

    The class is intentionally dataset-agnostic: callers may load cities5000,
    cities15000, allCountries filtered to desired feature classes, or a curated
    regional subset.  The dataset label/version is preserved in every result.
    """
    def __init__(self,path:Path,dataset:str='GeoNames'):
        self.path=Path(path);self.dataset=dataset;self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS places(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, asciiname TEXT,
          lat REAL NOT NULL, lon REAL NOT NULL, feature_class TEXT, feature_code TEXT,
          country_code TEXT, admin1 TEXT, population INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_places_lat_lon ON places(lat,lon);
        CREATE INDEX IF NOT EXISTS idx_places_country_admin ON places(country_code,admin1);
        CREATE TABLE IF NOT EXISTS countries(code TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admin1_names(code TEXT PRIMARY KEY, name TEXT NOT NULL);
        ''')

    @classmethod
    def from_geonames_data_pack(cls, db_path:Path, pack_root:Path, manifest_path:Path, *, city_file:str, country_file:str='countryInfo.txt', admin1_file:str='admin1CodesASCII.txt'):
        """Build a gazetteer only from a hash-verified, attributed offline data pack."""
        from .datapacks import load_data_pack_manifest, verify_data_pack
        pack_root=Path(pack_root);manifest=load_data_pack_manifest(manifest_path);check=verify_data_pack(pack_root,manifest,report_extras=False)
        if not check.ok:raise ValueError('GeoNames data-pack verification failed')
        if manifest.role!='geonames':raise ValueError(f'unexpected data-pack role: {manifest.role}')
        g=cls(db_path,dataset=manifest.identity)
        g.import_geonames_tsv(pack_root/city_file)
        if (pack_root/country_file).exists():g.import_country_info(pack_root/country_file)
        if (pack_root/admin1_file).exists():g.import_admin1_codes(pack_root/admin1_file)
        return g

    def add(self,*,id:int,name:str,lat:float,lon:float,country_code:str|None=None,admin1:str|None=None,population:int=0,feature_class:str='P',feature_code:str='PPL',asciiname:str|None=None):
        self.db.execute('INSERT OR REPLACE INTO places VALUES(?,?,?,?,?,?,?,?,?,?)',(id,name,asciiname,lat,lon,feature_class,feature_code,country_code,admin1,int(population or 0)))
    def commit(self):self.db.commit()
    def close(self):self.db.close()
    def import_geonames_tsv(self,path:Path,feature_classes:set[str]|None=None)->int:
        """Import the standard 19-column GeoNames geoname/cities*.txt layout."""
        count=0
        with Path(path).open('r',encoding='utf-8') as f:
            for line in f:
                row=line.rstrip('\n').split('\t')
                if len(row)<19:continue
                fc=row[6]
                if feature_classes and fc not in feature_classes:continue
                try:
                    self.add(id=int(row[0]),name=row[1],asciiname=row[2] or None,lat=float(row[4]),lon=float(row[5]),feature_class=fc,feature_code=row[7],country_code=row[8] or None,admin1=row[10] or None,population=int(row[14] or 0));count+=1
                except (ValueError,OverflowError):continue
        self.commit();return count

    def import_country_info(self,path:Path)->int:
        """Import GeoNames ``countryInfo.txt`` for human-readable country labels."""
        count=0
        with Path(path).open('r',encoding='utf-8') as f:
            for line in f:
                if not line.strip() or line.startswith('#'):continue
                row=line.rstrip('\n').split('\t')
                if len(row)<5 or not row[0] or not row[4]:continue
                self.db.execute('INSERT OR REPLACE INTO countries(code,name) VALUES(?,?)',(row[0],row[4]));count+=1
        self.commit();return count

    def import_admin1_codes(self,path:Path)->int:
        """Import GeoNames ``admin1CodesASCII.txt`` (for example ``JP.29`` -> Nara)."""
        count=0
        with Path(path).open('r',encoding='utf-8') as f:
            for line in f:
                row=line.rstrip('\n').split('\t')
                if len(row)<2 or not row[0] or not row[1]:continue
                self.db.execute('INSERT OR REPLACE INTO admin1_names(code,name) VALUES(?,?)',(row[0],row[1]));count+=1
        self.commit();return count

    def _country_name(self,code:str|None)->str|None:
        if not code:return None
        row=self.db.execute('SELECT name FROM countries WHERE code=?',(code,)).fetchone()
        return row[0] if row else None

    def _admin1_name(self,country_code:str|None,admin1:str|None)->str|None:
        if not country_code or not admin1:return None
        row=self.db.execute('SELECT name FROM admin1_names WHERE code=?',(f'{country_code}.{admin1}',)).fetchone()
        return row[0] if row else None

    def nearest(self,lat:float,lon:float,max_km:float=100.0)->PlaceEvidence|None:
        if not (-90<=lat<=90 and -180<=lon<=180):return None
        # Cheap latitude/longitude window before exact haversine. Widen longitude near poles.
        dlat=max_km/110.574;cos=max(0.01,math.cos(math.radians(lat)));dlon=max_km/(111.320*cos)
        rows=self.db.execute('''SELECT id,name,lat,lon,country_code,admin1,population,feature_class,feature_code
            FROM places WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?''',(lat-dlat,lat+dlat,lon-dlon,lon+dlon)).fetchall()
        candidates=[]
        for rid,name,la,lo,cc,a1,pop,fc,code in rows:
            d=haversine_km(lat,lon,la,lo)
            if d<=max_km:candidates.append((d,-int(pop or 0),rid,name,cc,a1,fc,code))
        if not candidates:return None
        # Distance dominates; population only breaks effectively equal-distance ties.
        candidates.sort(key=lambda x:(round(x[0],3),x[1],x[2]));d,_,rid,name,cc,a1,_,_=candidates[0]
        admin_name=self._admin1_name(cc,a1);country_name=self._country_name(cc)
        parts=[name]
        if admin_name and admin_name.casefold()!=name.casefold():parts.append(admin_name)
        elif a1 and not admin_name and a1!=name:parts.append(a1)
        if country_name:parts.append(country_name)
        elif cc:parts.append(cc)
        return PlaceEvidence(', '.join(parts),name,cc,a1,d,rid,self.dataset,country_name,admin_name)
