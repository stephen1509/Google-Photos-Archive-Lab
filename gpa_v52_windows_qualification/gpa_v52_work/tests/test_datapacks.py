from __future__ import annotations
import json
from pathlib import Path
import pytest

from gpa.datapacks import create_data_pack_manifest, write_data_pack_manifest, verify_data_pack, load_data_pack_manifest
from gpa.gazetteer import GeoNamesGazetteer
from gpa.timezones import boundary_dataset_from_data_pack, confidence_for_boundary
from gpa.model import Confidence


def test_data_pack_manifest_hashes_files_and_detects_tamper(tmp_path):
    root=tmp_path/'pack';root.mkdir();(root/'data.txt').write_text('ABC',encoding='utf-8')
    m=create_data_pack_manifest(root,role='test',name='sample',version='1',license='CC-BY',attribution='Example',source='https://example.invalid')
    mp=root/'manifest.json';write_data_pack_manifest(mp,m)
    assert verify_data_pack(root,mp).ok
    (root/'data.txt').write_text('CHANGED',encoding='utf-8')
    v=verify_data_pack(root,mp);assert not v.ok and v.mismatched==['data.txt']


def test_data_pack_manifest_requires_license_and_attribution(tmp_path):
    root=tmp_path/'pack';root.mkdir();(root/'x').write_text('x')
    with pytest.raises(ValueError):create_data_pack_manifest(root,role='x',name='n',version='1',license='',attribution='',source='s')


def test_data_pack_rejects_path_escape_in_manifest(tmp_path):
    root=tmp_path/'pack';root.mkdir();outside=tmp_path/'outside';outside.write_text('x')
    obj={'schema':'gpa.data-pack.v1','role':'test','name':'n','version':'1','license':'L','attribution':'A','source':'S','temporal_mode':None,'created_utc':'','files':[{'path':'../outside','size':1,'sha256':'0'*64}]}
    mp=root/'manifest.json';mp.write_text(json.dumps(obj))
    v=verify_data_pack(root,mp);assert not v.ok and '../outside' in v.unsafe_paths


def _geonames_row():
    # Standard 19-column GeoNames record for a point near Nara.
    return '\t'.join(['123','Nara','Nara','','34.6851','135.8048','P','PPLA','JP','','29','','','','360000','','','Asia/Tokyo','2026-01-01'])+'\n'


def test_geonames_gazetteer_builds_only_from_verified_pack_and_carries_manifest_identity(tmp_path):
    pack=tmp_path/'pack';pack.mkdir();(pack/'cities.txt').write_text(_geonames_row(),encoding='utf-8')
    (pack/'countryInfo.txt').write_text('JP\tJPN\t392\tJA\tJapan\n',encoding='utf-8')
    (pack/'admin1CodesASCII.txt').write_text('JP.29\tNara\tNara\t1855612\n',encoding='utf-8')
    m=create_data_pack_manifest(pack,role='geonames',name='GeoNames cities',version='2026-08-21',license='CC BY 4.0',attribution='GeoNames',source='https://download.geonames.org/export/dump/')
    mp=pack/'manifest.json';write_data_pack_manifest(mp,m)
    g=GeoNamesGazetteer.from_geonames_data_pack(tmp_path/'places.sqlite',pack,mp,city_file='cities.txt')
    e=g.nearest(34.6851,135.8048);g.close()
    assert e and e.label=='Nara, Japan' and e.dataset.startswith('geonames:GeoNames cities:2026-08-21:')
    (pack/'cities.txt').write_text('tampered')
    with pytest.raises(ValueError):GeoNamesGazetteer.from_geonames_data_pack(tmp_path/'bad.sqlite',pack,mp,city_file='cities.txt')


def test_timezone_boundary_dataset_from_verified_pack_enforces_temporal_scope(tmp_path):
    pack=tmp_path/'tz';pack.mkdir();(pack/'boundaries.geojson').write_text('{}')
    m=create_data_pack_manifest(pack,role='timezone-boundaries',name='timezone-boundary-builder',version='2026c',license='ODbL',attribution='timezone-boundary-builder/OpenStreetMap contributors',source='https://github.com/evansiroky/timezone-boundary-builder',temporal_mode='since1970')
    mp=pack/'manifest.json';write_data_pack_manifest(mp,m)
    ds=boundary_dataset_from_data_pack(pack,mp)
    assert ds.release=='2026c' and ds.temporal_mode=='since1970' and ds.sha256
    assert confidence_for_boundary(ds,2017)[0]==Confidence.PROVEN
    assert confidence_for_boundary(ds,1960)[0]==Confidence.PROBABLE


def test_boundary_dataset_identity_includes_exact_pack_hash_when_known(tmp_path):
    pack=tmp_path/'tz2';pack.mkdir();(pack/'data').write_text('x')
    m=create_data_pack_manifest(pack,role='timezone-boundaries',name='TBB',version='2026c',license='ODbL',attribution='OSM contributors',source='source',temporal_mode='since1970')
    mp=pack/'manifest.json';write_data_pack_manifest(mp,m)
    ds=boundary_dataset_from_data_pack(pack,mp)
    assert ':sha256-' in ds.identity and ds.sha256[:16] in ds.identity
