from __future__ import annotations
import io, json, os, zipfile, tempfile, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from gpa.model import *
from gpa.zipindex import *
from gpa.sidecars import *
from gpa.chronology import *
from gpa.review import *
from gpa.output import *
from gpa.catalog import *
from gpa.executor import RunExecutor
from gpa.audit import audit_archive

def mkzip(path:Path, entries:dict[str,bytes|str]):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for k,v in entries.items(): z.writestr(k,v.encode() if isinstance(v,str) else v)

def google_json(title='IMG_1.jpg',ts='1493596799',lat=34.68,lon=135.80,legacy=False,**extra):
    d={'title':title,'photoTakenTime':{'timestamp':ts},'creationTime':{'timestamp':str(int(ts)+123)},'description':'hello','favorited':True,'people':[{'name':'Alice'}]}
    d['geoInfo' if legacy else 'geoData']={'latitude':lat,'longitude':lon}
    d['geoInfoExif' if legacy else 'geoDataExif']={'latitude':lat+0.01,'longitude':lon+0.01}
    d.update(extra);return json.dumps(d)

@pytest.mark.parametrize('bad',['../x.jpg','../../x','/abs.jpg','C:/evil.jpg','..\\x.jpg'])
def test_safe_member_rejects_bad_paths(bad):
    with pytest.raises(ArchiveSafetyError):safe_member_path(bad)

@pytest.mark.parametrize('good',['Takeout/Google Photos/2017/a.jpg','a.jpg','日本語/写真.jpg','x/../y.jpg'])
def test_safe_member_accepts_normalized_paths(good):
    assert '..' not in safe_member_path(good).split('/')

def test_scan_and_cross_zip_sidecar(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip'
    mkzip(a,{'Takeout/Google Photos/Photos from 2017/IMG_1.jpg':b'jpeg'})
    mkzip(b,{'Takeout/Google Photos/Photos from 2017/IMG_1.jpg.json':google_json()})
    lib=ZipLibrary([a,b]);ms=lib.scan();media=[m for m in ms if m.suffix=='.jpg'][0]
    got=choose_sidecar(media,ms,lib)
    assert got and got[1].title=='IMG_1.jpg'

def test_different_folder_sidecar_not_matched(tmp_path):
    a=tmp_path/'x.zip';mkzip(a,{'A/IMG_1.jpg':b'x','B/IMG_1.jpg.json':google_json()})
    lib=ZipLibrary([a]);ms=lib.scan();media=[m for m in ms if m.suffix=='.jpg'][0]
    assert choose_sidecar(media,ms,lib) is None

@pytest.mark.parametrize('name',[
 'IMG_1.jpg.json','IMG_1.jpg.supplemental-metadata.json'])
def test_sidecar_naming_families(tmp_path,name):
    z=tmp_path/'z.zip';mkzip(z,{'D/IMG_1.jpg':b'x',f'D/{name}':google_json()})
    lib=ZipLibrary([z]);ms=lib.scan();media=[m for m in ms if m.suffix=='.jpg'][0]
    assert choose_sidecar(media,ms,lib)

def test_duplicate_marker_moved_to_sidecar_suffix(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/IMG_1(3516).jpg':b'x','D/IMG_1.jpg.supplemental-metadata(3516).json':google_json('IMG_1.jpg')})
    lib=ZipLibrary([z]);ms=lib.scan();media=[m for m in ms if m.suffix=='.jpg'][0]
    assert choose_sidecar(media,ms,lib)

def test_album_metadata_rejected(tmp_path):
    z=tmp_path/'z.zip';album=json.dumps({'title':'IMG_1.jpg','date':{'timestamp':'1'},'access':'protected'})
    mkzip(z,{'D/IMG_1.jpg':b'x','D/IMG_1.jpg.json':album})
    lib=ZipLibrary([z]);ms=lib.scan();media=[m for m in ms if m.suffix=='.jpg'][0]
    assert choose_sidecar(media,ms,lib) is None

def test_ambiguous_equal_sidecars_refused(tmp_path):
    z=tmp_path/'z.zip';j=google_json()
    mkzip(z,{'D/IMG_1.jpg':b'x','D/IMG_1.jpg.json':j,'D/IMG_1.jpg.supplemental-metadata.json':j})
    lib=ZipLibrary([z]);ms=lib.scan();media=[m for m in ms if m.suffix=='.jpg'][0]
    assert choose_sidecar(media,ms,lib) is None

def test_legacy_and_modern_geo_schema():
    for legacy in [False,True]:
        s=parse_google_sidecar(json.loads(google_json(legacy=legacy)))
        assert s.geo_current==(34.68,135.80)
        assert s.geo_exif==(34.69,135.81)

def test_zero_zero_geo_is_unknown():
    s=parse_google_sidecar(json.loads(google_json(lat=0,lon=0)))
    assert s.geo_current is None
    assert s.raw

def test_people_favorite_description_parse():
    s=parse_google_sidecar(json.loads(google_json()))
    assert s.people==['Alice'] and s.favorited is True and s.description=='hello'

def test_creation_time_not_used_as_taken_time():
    s=parse_google_sidecar(json.loads(google_json(ts='1000')))
    assert s.taken_epoch==1000 and s.creation_epoch==1123

@pytest.mark.parametrize('epoch,expected_unique',[
 (datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp(),True),
 (datetime(2017,5,1,1,tzinfo=timezone.utc).timestamp(),False),
 (datetime(2017,12,31,23,tzinfo=timezone.utc).timestamp(),False),
])
def test_month_invariance(epoch,expected_unique):
    months=month_candidates_for_utc_epoch(int(epoch))
    assert (len(months)==1)==expected_unique

def test_resolve_with_timezone():
    s=GoogleSidecar(taken_epoch=int(datetime(2017,4,30,16,tzinfo=timezone.utc).timestamp()))
    f=resolve_google_time(s,'Asia/Tokyo')
    assert (f.year,f.month,f.value.day)==(2017,5,1) and f.timezone_known

def test_resolve_unknown_timezone_safe_month():
    s=GoogleSidecar(taken_epoch=int(datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp()))
    f=resolve_google_time(s)
    assert f.precision==DatePrecision.MONTH and (f.year,f.month)==(2017,5)

def test_resolve_unknown_timezone_boundary_goes_range():
    s=GoogleSidecar(taken_epoch=int(datetime(2017,5,1,1,tzinfo=timezone.utc).timestamp()))
    f=resolve_google_time(s)
    assert f.precision==DatePrecision.RANGE and f.confidence==Confidence.PROBABLE

@pytest.mark.parametrize('local,offset',[
 (datetime(2017,5,1,21,0),timedelta(hours=9)),
 (datetime(2017,5,1,17,30),timedelta(hours=5,minutes=30)),
 (datetime(2017,5,1,8,0),timedelta(hours=-4)),
])
def test_derive_offset_from_camera(local,offset):
    epoch=int(datetime(2017,5,1,12,tzinfo=timezone.utc).timestamp())
    assert derive_offset_from_camera_local(epoch,local)==offset

def test_derive_offset_rejects_non_quarter_hour_clock_error():
    epoch=int(datetime(2017,5,1,12,tzinfo=timezone.utc).timestamp())
    assert derive_offset_from_camera_local(epoch,datetime(2017,5,1,21,7)) is None

def test_user_month_is_partial_not_fake_day():
    f=user_month(2017,5)
    assert f.precision==DatePrecision.MONTH and f.value is None and f.confidence==Confidence.USER_CONFIRMED
    assert placement_dir(f)=='Photos/2017/05 - May'

def test_user_year_needs_placement():
    f=user_year(2017)
    assert placement_dir(f)=='_Needs Placement/Date/2017-year-only'

def test_range_needs_placement():
    f=DateFact(DatePrecision.RANGE,Confidence.PROBABLE,start=datetime(2017,4,1),end=datetime(2017,5,31))
    assert placement_dir(f).startswith('_Needs Placement/Date/')

def test_unknown_needs_placement():
    assert placement_dir(DateFact(DatePrecision.UNKNOWN,Confidence.UNKNOWN))=='_Needs Placement/Date/Unknown'

def test_decision_journal_append_only(tmp_path):
    j=DecisionJournal(tmp_path/'j.jsonl')
    j.append(Decision('a','date',{'year':2017,'month':4},rationale='first recollection'))
    j.append(Decision('a','date',{'year':2017,'month':5},rationale='corrected after checking'))
    rows=j.read();assert len(rows)==2 and rows[0].value['month']==4 and rows[1].value['month']==5

@pytest.mark.parametrize('name,expected',[
 ('normal.jpg','normal.jpg'),('a:b?.jpg','a_b_.jpg'),('CON.jpg','_CON.jpg'),('LPT1','_LPT1'),('trail.','trail'),('日本語📷.jpg','日本語📷.jpg')])
def test_portable_filenames(name,expected):assert portable_filename(name)==expected

def test_long_name_deterministic_and_bounded():
    n='a'*300+'.jpg';a=portable_filename(n);b=portable_filename(n)
    assert a==b and len(a)<=180 and a.endswith('.jpg')

def test_different_long_names_different_output():
    assert portable_filename('a'*300+'.jpg')!=portable_filename('a'*299+'b.jpg')

def test_zip_sha256_and_source_unchanged(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'abc'});before=z.read_bytes()
    lib=ZipLibrary([z]);m=lib.scan()[0]
    assert lib.sha256(m)==hashlib.sha256(b'abc').hexdigest()
    assert z.read_bytes()==before

def test_duplicate_archive_member_path_rejected(tmp_path):
    z=tmp_path/'z.zip'
    with zipfile.ZipFile(z,'w') as f:
        f.writestr('a.jpg',b'a');f.writestr('a.jpg',b'b')
    with pytest.raises(ArchiveSafetyError):ZipLibrary([z]).scan()

def test_invalid_zip_rejected(tmp_path):
    z=tmp_path/'bad.zip';z.write_bytes(b'notzip')
    with pytest.raises(SourceIntegrityError):ZipLibrary([z]).scan()

def test_suspicious_compression_ratio_rejected(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'huge.txt':b'0'*100000})
    with pytest.raises(ArchiveSafetyError):ZipLibrary([z],max_ratio=10).scan()

def test_catalog_month_tags_and_near(tmp_path):
    c=Catalog(tmp_path/'c.sqlite')
    c.add(id='1',path='p1',year=2017,month=5,lat=34.6851,lon=135.8048,description='Nara Park',place='Nara',needs_review=0)
    c.add(id='2',path='p2',year=2017,month=6,lat=35.0116,lon=135.7681,description='Kyoto',place='Kyoto',needs_review=0)
    c.tag('1','album','Japan 2017');c.tag('1','person','Alice')
    assert c.by_month(2017,5)==[('1','p1')]
    assert c.tagged('album','Japan 2017')==[('1','p1')]
    near=c.near(34.6851,135.8048,5);assert near and near[0][0]=='1' and all(x[0]!='2' for x in near)

from gpa.fingerprint import *
from gpa.transactions import *
from gpa.revisions import *
from gpa.video import *
from gpa.timezones import *
import zlib, struct, subprocess, shutil

def jpeg_with_app1(meta=b'OLD',scan=b'\x01\x02\x03'):
    app=b'\xff\xe1'+(len(meta)+2).to_bytes(2,'big')+meta
    dqt=b'\xff\xdb\x00\x04\x00\x01'
    sos=b'\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00'
    return b'\xff\xd8'+app+dqt+sos+scan+b'\xff\xd9'

def png(chunks):
    out=b'\x89PNG\r\n\x1a\n'
    for typ,payload in chunks:
        out+=len(payload).to_bytes(4,'big')+typ+payload+zlib.crc32(typ+payload).to_bytes(4,'big')
    return out

def box(typ,payload):return (len(payload)+8).to_bytes(4,'big')+typ+payload

def test_jpeg_metadata_change_preserves_payload_fingerprint():
    a=jpeg_with_app1(b'A');b=jpeg_with_app1(b'B')
    assert hashlib.sha256(a).digest()!=hashlib.sha256(b).digest()
    assert jpeg_payload_fingerprint(a)==jpeg_payload_fingerprint(b)

def test_jpeg_scan_change_detected():
    assert jpeg_payload_fingerprint(jpeg_with_app1(scan=b'abc'))!=jpeg_payload_fingerprint(jpeg_with_app1(scan=b'abd'))

def test_bad_jpeg_rejected():
    with pytest.raises(MediaStructureError):jpeg_payload_fingerprint(b'no')

def test_png_xmp_change_preserves_visual_fingerprint():
    ihdr=(1).to_bytes(4,'big')+(1).to_bytes(4,'big')+bytes([8,2,0,0,0])
    a=png([(b'IHDR',ihdr),(b'iTXt',b'XML:com.adobe.xmp\x00A'),(b'IDAT',b'pixels'),(b'IEND',b'')])
    b=png([(b'IHDR',ihdr),(b'iTXt',b'XML:com.adobe.xmp\x00B'),(b'IDAT',b'pixels'),(b'IEND',b'')])
    assert png_visual_fingerprint(a)==png_visual_fingerprint(b)

def test_png_pixel_change_detected():
    ihdr=(1).to_bytes(4,'big')+(1).to_bytes(4,'big')+bytes([8,2,0,0,0])
    a=png([(b'IHDR',ihdr),(b'IDAT',b'A'),(b'IEND',b'')]);b=png([(b'IHDR',ihdr),(b'IDAT',b'B'),(b'IEND',b'')])
    assert png_visual_fingerprint(a)!=png_visual_fingerprint(b)

def test_mp4_metadata_change_preserves_mdat():
    a=box(b'ftyp',b'isom')+box(b'moov',b'A')+box(b'mdat',b'video')
    b=box(b'ftyp',b'isom')+box(b'moov',b'B')+box(b'mdat',b'video')
    assert mp4_mdat_fingerprint(a)==mp4_mdat_fingerprint(b)

def test_mp4_media_change_detected():
    a=box(b'ftyp',b'isom')+box(b'mdat',b'video1');b=box(b'ftyp',b'isom')+box(b'mdat',b'video2')
    assert mp4_mdat_fingerprint(a)!=mp4_mdat_fingerprint(b)

def test_mp4_without_mdat_rejected():
    with pytest.raises(MediaStructureError):mp4_mdat_fingerprint(box(b'ftyp',b'isom'))

def test_stage_copy_hash_and_commit(tmp_path):
    src=tmp_path/'src';src.write_bytes(b'hello');h=hashlib.sha256(b'hello').hexdigest()
    stage=stage_copy(src,tmp_path/'destdir',h);dest=tmp_path/'destdir'/'final.jpg'
    commit_no_overwrite(stage,dest);assert dest.read_bytes()==b'hello' and not stage.exists()

def test_stage_copy_bad_expected_hash_cleans_up(tmp_path):
    src=tmp_path/'src';src.write_bytes(b'hello')
    with pytest.raises(CommitError):stage_copy(src,tmp_path/'d','0'*64)
    assert not list((tmp_path/'d').glob('.gpa-stage-*'))

def test_commit_never_overwrites(tmp_path):
    staged=tmp_path/'s';staged.write_bytes(b'new');dest=tmp_path/'d';dest.write_bytes(b'old')
    with pytest.raises(FileExistsError):commit_no_overwrite(staged,dest)
    assert dest.read_bytes()==b'old' and staged.exists()

def test_write_json_new_never_overwrites(tmp_path):
    p=tmp_path/'x.json';write_json_new(p,{'a':1})
    with pytest.raises(FileExistsError):write_json_new(p,{'a':2})
    assert json.loads(p.read_text())=={'a':1}

def test_revision_store_immutable_history(tmp_path):
    r=RevisionStore(tmp_path);p1=r.add_revision('asset',{'date':'2017-05'});id1=r.current('asset')
    p2=r.add_revision('asset',{'date':'2017-06'});id2=r.current('asset')
    assert id1!=id2 and p1.exists() and p2.exists() and len(r.revisions('asset'))==2

def test_revision_store_idempotent_same_record(tmp_path):
    r=RevisionStore(tmp_path);p1=r.add_revision('a',{'x':1});p2=r.add_revision('a',{'x':1})
    assert p1==p2 and len(r.revisions('a'))==1

def test_ffprobe_real_tiny_video_if_ffmpeg_available(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg unavailable')
    p=tmp_path/'v.mp4'
    cp=subprocess.run([ffmpeg,'-y','-f','lavfi','-i','color=c=black:s=16x16:d=0.1','-c:v','libx264','-pix_fmt','yuv420p',str(p)],capture_output=True,text=True)
    if cp.returncode:pytest.skip('libx264 unavailable')
    pr=probe_video(p);assert has_supported_video_track(pr)

def test_probe_supported_codecs():
    for c in ['h264','hevc','av1']:assert has_supported_video_track({'streams':[{'codec_type':'video','codec_name':c}]})
    assert not has_supported_video_track({'streams':[{'codec_type':'video','codec_name':'vp9'}]})

def test_timezone_resolver_historical_zoneinfo():
    r=OfflineTimezoneResolver();dt=r.local_from_epoch(int(datetime(2017,5,1,0,tzinfo=timezone.utc).timestamp()),'Asia/Tokyo')
    assert dt.hour==9 and dt.utcoffset()==timedelta(hours=9)

from gpa.motion import *
from gpa.writepolicy import *

def test_motion_flag_without_video_is_not_motion():
    data=jpeg_with_app1(b'<xmp Camera:MotionPhoto="1"/>')
    assert locate_jpeg_motion_video(data,100) is None

def test_jpeg_motion_located_by_declared_video_length():
    still=jpeg_with_app1(b'xmp')
    video=box(b'ftyp',b'isom')+box(b'moov',b'x')+box(b'mdat',b'abc')
    data=still+b'GAINMAP_OR_OTHER_BYTES'+video
    got=locate_jpeg_motion_video(data,len(video));assert got and data[got.start:got.end]==video

def test_jpeg_motion_wrong_length_rejected():
    still=jpeg_with_app1();video=box(b'ftyp',b'isom')+box(b'mdat',b'abc')
    assert locate_jpeg_motion_video(still+video,len(video)-1) is None

def test_heif_mpvd_must_be_last_box_and_contain_video():
    video=box(b'ftyp',b'isom')+box(b'moov',b'x')+box(b'mdat',b'abc')
    data=box(b'ftyp',b'heic')+box(b'meta',b'x')+box(b'mpvd',video)
    got=locate_heif_motion_video(data);assert got and data[got.start:got.end]==video

def test_heif_mpvd_not_last_rejected():
    video=box(b'ftyp',b'isom')+box(b'mdat',b'abc')
    data=box(b'ftyp',b'heic')+box(b'mpvd',video)+box(b'free',b'x')
    assert locate_heif_motion_video(data) is None

def test_live_photo_pair_requires_identifier_not_basename():
    assert live_photo_pair('ABC','ABC')
    assert not live_photo_pair('ABC','XYZ')
    assert not live_photo_pair(None,'ABC')

def test_write_policy_partial_month_not_exif():
    f=user_month(2017,5);p=plan_metadata(f,None)
    assert 'DateTimeOriginal' not in p.exif and p.xmp['XMP-photoshop:DateCreated']=='2017-05'

def test_write_policy_exact_datetime_and_offset():
    dt=datetime(2017,5,1,21,30,tzinfo=timezone(timedelta(hours=9)))
    f=DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=dt,year=2017,month=5,timezone_known=True,source='test')
    p=plan_metadata(f,None);assert p.exif['DateTimeOriginal']=='2017:05:01 21:30:00' and p.exif['OffsetTimeOriginal']=='+09:00'

def test_write_policy_range_does_not_invent_exact_date():
    f=DateFact(DatePrecision.RANGE,Confidence.PROBABLE,start=datetime(2017,4,1),end=datetime(2017,5,31))
    p=plan_metadata(f,None);assert 'DateTimeOriginal' not in p.exif and 'XMP-photoshop:DateCreated' not in p.xmp and p.warnings

def test_text_location_never_invents_gps():
    p=plan_metadata(None,None,location_label='Nara')
    assert p.archival['location_label']=='Nara' and not any(k.startswith('GPS') for k in p.exif) and p.warnings

def test_exact_gps_written_when_known():
    p=plan_metadata(None,None,exact_gps=(34.68,135.80))
    assert p.exif['GPSLatitudeRef']=='N' and p.exif['GPSLongitudeRef']=='E'

def test_google_favorite_not_mapped_to_rating():
    s=GoogleSidecar(favorited=True,raw={'favorited':True});p=plan_metadata(None,s)
    assert p.archival['google']['favorited'] is True and all('Rating' not in k for k in p.xmp)

def test_raw_google_json_retained_in_archival_plan():
    raw={'mysteryFutureField':{'x':1}};s=GoogleSidecar(raw=raw);p=plan_metadata(None,s)
    assert p.archival['google']['raw']==raw

from gpa.indexed_sidecars import SidecarIndex
from gpa.planner import LibraryPlanner

def test_batch_json_across_archives(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip';mkzip(a,{'D/1.json':'{"x":1}'});mkzip(b,{'D/2.json':'{"x":2}'})
    lib=ZipLibrary([a,b]);ms=lib.scan();got=lib.batch_json(ms)
    assert got[(str(a),'D/1.json')]['x']==1 and got[(str(b),'D/2.json')]['x']==2

def test_indexed_sidecar_cross_zip(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip';mkzip(a,{'D/IMG_1.jpg':b'x'});mkzip(b,{'D/IMG_1.jpg.json':google_json()})
    lib=ZipLibrary([a,b]);ms=lib.scan();idx=SidecarIndex(ms,lib);m=[x for x in ms if x.suffix=='.jpg'][0]
    assert idx.choose(m)[1].title=='IMG_1.jpg'

def test_end_to_end_exact_duplicate_collapses_and_retains_occurrences(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip';j=google_json(ts=str(int(datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp())))
    mkzip(a,{'Takeout/Google Photos/Photos from 2017/IMG_1.jpg':b'SAME','Takeout/Google Photos/Photos from 2017/IMG_1.jpg.json':j})
    mkzip(b,{'Takeout/Google Photos/Holiday/IMG_1.jpg':b'SAME','Takeout/Google Photos/Holiday/IMG_1.jpg.json':j})
    assets=LibraryPlanner([a,b]).plan();assert len(assets)==1 and len(assets[0].occurrences)==2
    assert assets[0].output_dir=='Photos/2017/05 - May'

def test_same_filename_different_bytes_remains_two_assets(tmp_path):
    a=tmp_path/'a.zip';mkzip(a,{'A/IMG.jpg':b'A','B/IMG.jpg':b'B'})
    assert len(LibraryPlanner([a]).plan())==2

def test_duplicate_conflicting_months_goes_review(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip'
    j1=google_json('IMG.jpg',ts=str(int(datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp())))
    j2=google_json('IMG.jpg',ts=str(int(datetime(2017,6,15,12,tzinfo=timezone.utc).timestamp())))
    mkzip(a,{'A/IMG.jpg':b'SAME','A/IMG.jpg.json':j1});mkzip(b,{'B/IMG.jpg':b'SAME','B/IMG.jpg.json':j2})
    x=LibraryPlanner([a,b]).plan()[0]
    assert x.date_fact.confidence==Confidence.CONFLICT and x.output_dir.endswith('/Unknown') and x.warnings

def test_no_json_never_uses_export_year(tmp_path):
    a=tmp_path/'takeout-2026.zip';mkzip(a,{'Takeout/Google Photos/Photos from 2026/oldscan.jpg':b'X'})
    x=LibraryPlanner([a]).plan()[0]
    assert x.date_fact.precision==DatePrecision.UNKNOWN and x.output_dir.endswith('/Unknown')

from gpa.exiftool import *

def fake_exiftool(tmp_path,version='13.59'):
    p=tmp_path/'fake_exiftool.py'
    p.write_text(f'''#!/usr/bin/env python3\nimport sys,json\nif "-ver" in sys.argv: print("{version}"); raise SystemExit(0)\nif "-j" in sys.argv: print(json.dumps([{{"EXIF:DateTimeOriginal":"2017:05:01 12:00:00"}}])); raise SystemExit(0)\nprint("1 image files updated")\n''')
    p.chmod(0o755);return p

def test_exiftool_pinned_version_accepts_approved(tmp_path):
    e=ExifToolAdapter(fake_exiftool(tmp_path));assert e.version()=='13.59'

def test_exiftool_pinned_version_rejects_drift(tmp_path):
    e=ExifToolAdapter(fake_exiftool(tmp_path,'13.60'))
    with pytest.raises(ExifToolError):e.version()

def test_exiftool_read_json_shape(tmp_path):
    e=ExifToolAdapter(fake_exiftool(tmp_path));d=e.read(tmp_path/'a.jpg');assert d['EXIF:DateTimeOriginal'].startswith('2017')

def test_exiftool_write_args_capture_date_semantics(tmp_path):
    e=ExifToolAdapter(fake_exiftool(tmp_path));f=user_month(2017,5);p=plan_metadata(f,None)
    args=e.write_args(tmp_path/'a.jpg',p)
    assert '-XMP-photoshop:DateCreated=2017-05' in args and not any('XMP-xmp:CreateDate=' in x for x in args)

from gpa.storage import *
from gpa.ledger import *

def test_fat32_large_file_blocked(tmp_path):
    p=preflight_destination(tmp_path,[5*1024**3],fs_type='FAT32');assert not p.ok and any('4 GiB' in x for x in p.errors)

def test_storage_requires_final_plus_largest_staging(tmp_path):
    p=preflight_destination(tmp_path,[100,200,300]);assert p.required_bytes==900

def test_windows_path_risk_component_and_total():
    assert windows_path_risk('a/'+'b'*256+'.jpg')
    assert windows_path_risk('a/'*130+'x.jpg')

def test_ledger_resume_states(tmp_path):
    l=Ledger(tmp_path/'l.db');s=tmp_path/'stage';d=tmp_path/'dest'
    l.set('a','STAGED',s,d);assert classify_recovery(l.get('a'))=='restart_asset'
    s.write_bytes(b'x');assert classify_recovery(l.get('a'))=='resume_from_staged'
    d.write_bytes(b'x');assert classify_recovery(l.get('a'))=='verify_destination_before_marking_committed'

def test_ledger_committed_not_resumable(tmp_path):
    l=Ledger(tmp_path/'l.db');l.set('a','COMMITTED');l.set('b','FAILED',error='x')
    assert [x[0] for x in l.resumable()]==['b']

def test_real_ffmpeg_metadata_remux_preserves_mdat(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg unavailable')
    a=tmp_path/'a.mp4';b=tmp_path/'b.mp4'
    c1=subprocess.run([ffmpeg,'-y','-f','lavfi','-i','testsrc=size=32x32:rate=5:duration=0.4','-c:v','libx264','-pix_fmt','yuv420p',str(a)],capture_output=True)
    if c1.returncode:pytest.skip('libx264 unavailable')
    c2=subprocess.run([ffmpeg,'-y','-i',str(a),'-map','0','-c','copy','-metadata','title=changed',str(b)],capture_output=True)
    assert c2.returncode==0
    da,db=a.read_bytes(),b.read_bytes()
    assert hashlib.sha256(da).digest()!=hashlib.sha256(db).digest()
    assert mp4_mdat_fingerprint(da)==mp4_mdat_fingerprint(db)
    assert has_supported_video_track(probe_video(b))

def test_observed_51_char_google_sidecar_rule_examples():
    cases={
      'IMG_9844-PHOTO_FRAME.jpg':'IMG_9844-PHOTO_FRAME.jpg.supplemental-metadata.json',
      'RPReplay_Final1740935464.mp4':'RPReplay_Final1740935464.mp4.supplemental-meta.json',
      'Screen Shot 2017-06-14 at 16.04.53.png':'Screen Shot 2017-06-14 at 16.04.53.png.supplem.json',
      '370C4A47-20E7-42CE-A380-8C05DC43BD03.png':'370C4A47-20E7-42CE-A380-8C05DC43BD03.png.suppl.json',
      '70525914786__50E58EDA-7955-48C0-AC76-7951749D7':'70525914786__50E58EDA-7955-48C0-AC76-7951749D7.json',
    }
    for media,sidecar in cases.items():assert sidecar in expected_sidecar_names(media)

def test_observed_duplicate_marker_swap_rule():
    names=expected_sidecar_names('camphoto_1271212614(2).jpg')
    assert 'camphoto_1271212614.jpg.supplemental-metadata(2).json' in names

def test_no_loose_prefix_false_match(tmp_path):
    z=tmp_path/'z.zip';media='A'*60+'.jpg';wrong=('A'*46)+'.json'
    mkzip(z,{f'D/{media}':b'x',f'D/{wrong}':google_json('totally-different.jpg')})
    lib=ZipLibrary([z]);ms=lib.scan();m=[x for x in ms if x.suffix=='.jpg'][0]
    # This filename can be a deterministic truncation candidate, but conflicting title must lower it below auto-match threshold.
    assert choose_sidecar(m,ms,lib) is None

from gpa.embedded import *
from gpa.location import resolve_location, LocationPolicy

def test_embedded_exiftool_normalizes_camera_datetime_offset_gps_device_and_live_id():
    tags={
        'EXIF:DateTimeOriginal':'2017:05:01 21:30:00.25','EXIF:OffsetTimeOriginal':'+09:00',
        'EXIF:GPSLatitude':34.6851,'EXIF:GPSLongitude':135.8048,
        'EXIF:Make':'Apple','EXIF:Model':'iPhone','Apple:ContentIdentifier':'ABC'
    }
    e=embedded_from_exiftool(tags,kind='still')
    assert e.capture_local==datetime(2017,5,1,21,30,0,250000)
    assert e.offset==timedelta(hours=9) and e.gps==(34.6851,135.8048)
    assert e.make=='Apple' and e.model=='iPhone' and e.content_identifier=='ABC'

def test_embedded_zero_zero_gps_is_unknown_not_real_location():
    e=embedded_from_exiftool({'EXIF:GPSLatitude':0,'EXIF:GPSLongitude':0})
    assert e.gps is None and e.warnings

def test_embedded_camera_without_timezone_only_proves_month_not_fake_instant():
    e=embedded_from_exiftool({'EXIF:DateTimeOriginal':'2017:05:31 23:59:59'})
    f=camera_date_fact(e)
    assert f.precision==DatePrecision.MONTH and (f.year,f.month)==(2017,5) and not f.timezone_known and f.value is None

def test_google_and_camera_can_prove_missing_offset_and_exact_local_instant():
    epoch=int(datetime(2017,5,1,12,30,tzinfo=timezone.utc).timestamp())
    sc=GoogleSidecar(taken_epoch=epoch)
    e=EmbeddedMetadata(capture_local=datetime(2017,5,1,21,30))
    f=reconcile_google_and_camera([sc],e)
    assert f.precision==DatePrecision.INSTANT and f.timezone_known
    assert f.value.utcoffset()==timedelta(hours=9) and f.value.hour==21

def test_google_camera_cross_month_conflict_never_silently_chooses_one():
    epoch=int(datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp())
    sc=GoogleSidecar(taken_epoch=epoch)
    e=EmbeddedMetadata(capture_local=datetime(2017,6,2,9,0))
    f=reconcile_google_and_camera([sc],e)
    assert f.confidence==Confidence.CONFLICT and f.precision==DatePrecision.UNKNOWN

def test_embedded_movie_content_identifier_uses_quicktime_keys_not_still_makernote():
    e=embedded_from_exiftool({'Keys:ContentIdentifier':'MOV-ID','Apple:ContentIdentifier':'WRONG'},kind='movie')
    assert e.content_identifier=='MOV-ID'

def _jpeg_with_exif_datetime(dt='2016:07:08 09:10:11',offset=None):
    from io import BytesIO
    from PIL import Image
    im=Image.new('RGB',(2,2),(10,20,30));ex=Image.Exif();ex[36867]=dt
    if offset:ex[36881]=offset
    b=BytesIO();im.save(b,format='JPEG',exif=ex);return b.getvalue()

def test_pillow_embedded_reader_gets_datetime_without_pixel_decode_requirement():
    e=embedded_from_pillow_bytes(_jpeg_with_exif_datetime())
    assert e and e.capture_local==datetime(2016,7,8,9,10,11)

def test_planner_uses_embedded_camera_month_when_google_json_missing(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'Takeout/Google Photos/old.jpg':_jpeg_with_exif_datetime('2016:07:08 09:10:11')})
    a=LibraryPlanner([z],embedded_provider=PillowZipEmbeddedProvider()).plan()[0]
    assert a.output_dir=='Photos/2016/07 - July'
    assert a.date_fact.source=='embedded-camera-local-month' and a.date_fact.value is None

def test_planner_google_plus_embedded_can_prove_exact_timezone(tmp_path):
    epoch=int(datetime(2016,7,8,0,10,11,tzinfo=timezone.utc).timestamp())
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':_jpeg_with_exif_datetime('2016:07:08 09:10:11'),'D/a.jpg.json':google_json('a.jpg',str(epoch))})
    a=LibraryPlanner([z],embedded_provider=PillowZipEmbeddedProvider()).plan()[0]
    assert a.date_fact.precision==DatePrecision.INSTANT and a.date_fact.value.utcoffset()==timedelta(hours=9)

def test_planner_google_camera_month_conflict_goes_review(tmp_path):
    epoch=int(datetime(2016,7,8,0,tzinfo=timezone.utc).timestamp())
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':_jpeg_with_exif_datetime('2016:08:08 09:10:11'),'D/a.jpg.json':google_json('a.jpg',str(epoch))})
    a=LibraryPlanner([z],embedded_provider=PillowZipEmbeddedProvider()).plan()[0]
    assert a.date_fact.confidence==Confidence.CONFLICT and a.output_dir.endswith('/Unknown')

def test_location_policy_can_use_embedded_camera_gps_without_google_location():
    e=EmbeddedMetadata(gps=(34.68,135.80))
    f=resolve_location([],LocationPolicy.PREFER_GOOGLE_CURRENT,e.gps)
    assert f.exact and f.source=='embedded-camera' and (f.lat,f.lon)==e.gps

def test_location_strict_conflict_google_vs_embedded_writes_no_choice():
    s=GoogleSidecar(geo_current=(34.68,135.8))
    f=resolve_location([s],LocationPolicy.REQUIRE_AGREEMENT,(35.0,136.0))
    assert f.confidence==Confidence.CONFLICT and f.lat is None and f.embedded_candidates

from gpa.gazetteer import *

def _geonames_row(id,name,lat,lon,cc='JP',admin1='29',pop=1000,fc='P',code='PPL'):
    # standard 19 columns
    r=[str(id),name,name,'',str(lat),str(lon),fc,code,cc,'',admin1,'','','',str(pop),'0','','','2026-01-01']
    return '\t'.join(r)

def test_geonames_import_and_nearest_place_is_derived_not_coordinate_replacement(tmp_path):
    src=tmp_path/'cities.txt';src.write_text(_geonames_row(1,'Nara',34.6851,135.8048,pop=350000)+'\n'+_geonames_row(2,'Kyoto',35.0116,135.7681,cc='JP',admin1='26',pop=1400000)+'\n',encoding='utf-8')
    g=GeoNamesGazetteer(tmp_path/'g.db','GeoNames synthetic 2026-08');assert g.import_geonames_tsv(src)==2
    p=g.nearest(34.69,135.81,20);g.close()
    assert p and p.name=='Nara' and p.confidence=='derived' and 'GPS remains authoritative' in p.note

def test_geonames_nearest_none_outside_requested_radius(tmp_path):
    g=GeoNamesGazetteer(tmp_path/'g.db');g.add(id=1,name='Nara',lat=34.6851,lon=135.8048,country_code='JP');g.commit()
    assert g.nearest(35.7,139.7,10) is None;g.close()

def test_geonames_distance_beats_population_for_place_label(tmp_path):
    g=GeoNamesGazetteer(tmp_path/'g.db');g.add(id=1,name='NearTown',lat=34.68,lon=135.80,country_code='JP',population=100)
    g.add(id=2,name='HugeCity',lat=34.90,lon=135.80,country_code='JP',population=10000000);g.commit()
    assert g.nearest(34.681,135.801,50).name=='NearTown';g.close()

def test_geonames_import_can_filter_feature_class(tmp_path):
    src=tmp_path/'all.txt';src.write_text(_geonames_row(1,'City',34,135,fc='P')+'\n'+_geonames_row(2,'Mountain',34,135,fc='T',code='MT')+'\n')
    g=GeoNamesGazetteer(tmp_path/'g.db');assert g.import_geonames_tsv(src,{'P'})==1
    assert g.db.execute('select count(*) from places').fetchone()[0]==1;g.close()

def test_invalid_reverse_geocode_coordinate_returns_none(tmp_path):
    g=GeoNamesGazetteer(tmp_path/'g.db');assert g.nearest(100,0) is None;g.close()

def test_since1970_boundary_is_proven_for_2017_but_not_1960():
    ds=BoundaryDataset('timezone-boundary-builder','2026c','since1970','abc')
    assert confidence_for_boundary(ds,2017)[0]==Confidence.PROVEN
    assert confidence_for_boundary(ds,1960)[0]==Confidence.PROBABLE

def test_current_timezone_boundary_never_auto_upgrades_historical_fact():
    ds=BoundaryDataset('timezone-boundary-builder','2026c','current')
    conf,note=confidence_for_boundary(ds,2017)
    assert conf==Confidence.PROBABLE and 'not authoritative' in note

def test_tzdata_version_is_recordable_or_explicitly_unavailable():
    v=system_tzdata_version();assert v is None or isinstance(v,str)

def test_proven_timezone_evidence_records_boundary_and_tzdata_provenance():
    epoch=int(datetime(2017,5,1,0,tzinfo=timezone.utc).timestamp());s=GoogleSidecar(taken_epoch=epoch)
    e=TimezoneEvidence('Asia/Tokyo',Confidence.PROVEN,'timezone-boundary-builder:2026c:since1970','since-1970 proof','2026c')
    f,suggested=resolve_with_timezone_evidence(s,e)
    assert f.value.hour==9 and '2026c:since1970' in f.source and 'tzdata=2026c' in f.note

from gpa.verification import *
from gpa.mirror import *

def test_migration_verification_separates_archive_integrity_from_takeout_completeness(tmp_path):
    root=tmp_path/'archive';(root/'metadata'/'assets').mkdir(parents=True);(root/'metadata'/'runs').mkdir(parents=True)
    # Audit root with zero assets is structurally valid enough for this policy test.
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':[{'path':'x.zip','sha256':'a'*64}], 'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    v=verify_migration(root)
    assert v.archive_integrity_verified and not v.google_retirement_ready
    assert any('every Google Takeout archive part' in x for x in v.blockers)

def test_google_retirement_gate_requires_confirmed_parts_redundant_copy_and_source_vault(tmp_path):
    from gpa.source_vault import vault_source_archive
    from gpa.transactions import sha256_file
    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw takeout evidence');digest=sha256_file(raw)
    root=tmp_path/'archive';(root/'metadata'/'assets').mkdir(parents=True);(root/'metadata'/'runs').mkdir(parents=True)
    from gpa.archive_format import upgrade_archive_format
    upgrade_archive_format(root)
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':[{'path':str(raw),'sha256':digest}], 'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    vault=tmp_path/'vault';vault_source_archive(raw,vault)
    assert not verify_migration(root,all_takeout_parts_confirmed=True,redundant_copy_verified=True).google_retirement_ready
    v=verify_migration(root,all_takeout_parts_confirmed=True,redundant_copy_verified=True,source_vault_root=vault,require_source_vault_redundancy=False,require_takeout_receipt=False,require_exit_evidence=False,require_independent_media=False)
    assert v.google_retirement_ready and v.source_vault_verified and v.source_vault_records==1

def test_partial_run_blocks_retirement_even_if_copy_confirmed(tmp_path):
    root=tmp_path/'archive';(root/'metadata'/'assets').mkdir(parents=True);(root/'metadata'/'runs').mkdir(parents=True)
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'partial','source_archives':[{'path':'x.zip','sha256':'a'*64}], 'source_problems':[{'x':1}], 'assets_planned':1,'assets_committed':0,'failures':[{'x':1}],'unclassified_preserved':[]}))
    v=verify_migration(root,all_takeout_parts_confirmed=True,redundant_copy_verified=True);assert not v.google_retirement_ready

def test_tree_manifest_and_verified_mirror_detect_damage_and_missing(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir();(a/'x').write_bytes(b'abc');(a/'y').write_bytes(b'def')
    (b/'x').write_bytes(b'abc');(b/'y').write_bytes(b'WRONG')
    m=build_tree_manifest(a);r=verify_mirror(m,b);assert not r.ok and r.mismatched==['y']
    (b/'y').write_bytes(b'def');assert verify_mirror(m,b).ok
    (b/'x').unlink();assert verify_mirror(m,b).missing==['x']

def test_tree_manifest_is_content_addressed_not_mtime_based(tmp_path):
    r=tmp_path/'r';r.mkdir();p=r/'x';p.write_bytes(b'abc');m=build_tree_manifest(r);p.touch()
    assert verify_mirror(m,r).ok

def test_end_to_end_no_json_real_jpeg_uses_embedded_month_and_preserves_bytes(tmp_path):
    media=_jpeg_with_exif_datetime('2014:11:23 18:45:12')
    z=tmp_path/'takeout.zip';mkzip(z,{'Takeout/Google Photos/old/scan.jpg':media})
    root=tmp_path/'archive';run=RunExecutor([z],root).execute(run_id='embedded-e2e')
    assert run['status']=='complete' and run['assets_committed']==1
    out=root/'Photos'/'2014'/'11 - November'/'scan.jpg'
    assert out.read_bytes()==media
    assert hashlib.sha256(out.read_bytes()).hexdigest()==hashlib.sha256(media).hexdigest()
    xp=Path(str(out)+'.xmp');assert xp.exists() and b'2014-11' in xp.read_bytes()
    assert audit_archive(root).ok

def test_default_planner_embedded_fallback_does_not_use_filesystem_timestamp(tmp_path):
    z=tmp_path/'takeout-2026.zip';mkzip(z,{'Photos/no_meta.jpg':_jpeg_with_exif_datetime('2009:02:03 04:05:06')})
    a=LibraryPlanner([z]).plan()[0]
    assert a.output_dir=='Photos/2009/02 - February' and a.date_fact.source=='embedded-camera-local-month'

def test_ffprobe_normalizer_reads_live_content_id_and_iso6709_but_not_creation_time():
    probe={'format':{'tags':{'com.apple.quicktime.content.identifier':'LIVE-1','com.apple.quicktime.location.ISO6709':'+34.6851+135.8048+000.0/','creation_time':'2017-05-01T00:00:00Z','com.apple.quicktime.make':'Apple'}}}
    e=embedded_from_ffprobe(probe)
    assert e.content_identifier=='LIVE-1' and e.gps==(34.6851,135.8048) and e.make=='Apple'
    assert e.capture_local is None and any('creation_time' in w for w in e.warnings)

def test_ffprobe_zero_zero_iso6709_not_promoted_to_real_gps():
    e=embedded_from_ffprobe({'format':{'tags':{'com.apple.quicktime.location.ISO6709':'+0.0+0.0+0.0/'}}})
    assert e.gps is None and e.warnings

def test_planner_auto_live_photo_pair_uses_content_id_for_placement_only(tmp_path):
    class FakeProvider:
        def __call__(self,m,lib):
            if m.suffix=='.jpg':return EmbeddedMetadata(capture_local=datetime(2018,3,10,12),content_identifier='CID')
            if m.suffix=='.mov':return EmbeddedMetadata(content_identifier='CID')
    z=tmp_path/'z.zip';mkzip(z,{'D/still.jpg':b'notrealjpeg','D/completely-different-name.mov':b'notrealmov'})
    assets=LibraryPlanner([z],embedded_provider=FakeProvider()).plan();by={PurePosixPath(a.occurrences[0].path).suffix.lower():a for a in assets}
    assert by['.jpg'].output_dir=='Photos/2018/03 - March' and by['.mov'].output_dir=='Photos/2018/03 - March'
    assert by['.mov'].date_fact.precision==DatePrecision.UNKNOWN
    assert by['.mov'].placement_fact.source=='live-photo-companion-placement'

def test_planner_does_not_pair_same_basename_without_content_identifier(tmp_path):
    class EmptyProvider:
        def __call__(self,m,lib):return EmbeddedMetadata(capture_local=datetime(2018,3,10,12) if m.suffix=='.jpg' else None)
    z=tmp_path/'z.zip';mkzip(z,{'D/IMG_1.jpg':b'A','D/IMG_1.mov':b'B'})
    assets=LibraryPlanner([z],embedded_provider=EmptyProvider()).plan();mov=[a for a in assets if a.occurrences[0].path.endswith('.mov')][0]
    assert mov.output_dir.endswith('/Unknown') and not mov.family_relations

def test_real_ffprobe_quicktime_content_identifier_and_location_roundtrip(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg unavailable')
    mov=tmp_path/'live.mov'
    cp=subprocess.run([ffmpeg,'-y','-f','lavfi','-i','color=size=32x32:rate=5:duration=0.4','-c:v','libx264','-pix_fmt','yuv420p','-movflags','use_metadata_tags','-metadata','com.apple.quicktime.content.identifier=REAL-LIVE-ID','-metadata','com.apple.quicktime.location.ISO6709=+34.6851+135.8048+000.0/',str(mov)],capture_output=True)
    if cp.returncode:pytest.skip('ffmpeg libx264 unavailable')
    probe=probe_video(mov);e=embedded_from_ffprobe(probe)
    assert e.content_identifier=='REAL-LIVE-ID' and e.gps==(34.6851,135.8048)
    z=tmp_path/'z.zip';mkzip(z,{'D/live.mov':mov.read_bytes()});lib=ZipLibrary([z]);m=lib.scan()[0]
    e2=FFprobeZipEmbeddedProvider()(m,lib);assert e2 and e2.content_identifier=='REAL-LIVE-ID' and e2.gps==e.gps



def _motion_base_jpeg():
    from io import BytesIO
    from PIL import Image
    im=Image.new('RGB',(2,2),(1,2,3));b=BytesIO();im.save(b,format='JPEG');return b.getvalue()

_MOTION_XMP_LOCAL="""<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:Camera="http://ns.google.com/photos/1.0/camera/" xmlns:Container="http://ns.google.com/photos/1.0/container/" xmlns:Item="http://ns.google.com/photos/1.0/container/item/"><rdf:RDF><rdf:Description Camera:MotionPhoto="1" Camera:MotionPhotoVersion="1"><Container:Directory><rdf:Seq><rdf:li rdf:parseType="Resource" Item:Mime="image/jpeg" Item:Semantic="Primary" Item:Padding="0"/><rdf:li rdf:parseType="Resource" Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="{length}"/></rdf:Seq></Container:Directory></rdf:Description></rdf:RDF></x:xmpmeta>"""

def _inject_standard_xmp(jpeg:bytes,xmp:bytes)->bytes:
    payload=b'http://ns.adobe.com/xap/1.0/\x00'+xmp
    seg=b'\xff\xe1'+(len(payload)+2).to_bytes(2,'big')+payload
    assert jpeg.startswith(b'\xff\xd8')
    return jpeg[:2]+seg+jpeg[2:]

def test_jpeg_xmp_packet_extractor_stops_before_entropy_data():
    j=_inject_standard_xmp(_motion_base_jpeg(),b'<x>ok</x>')
    assert jpeg_xmp_packets(j)==[b'<x>ok</x>']

def test_jpeg_xmp_malformed_segment_length_fails_closed():
    j=b'\xff\xd8\xff\xe1\xff\xffbad'
    assert jpeg_xmp_packets(j)==[]

def test_real_motion_photo_jpeg_with_ffmpeg_video_detects_structurally(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg unavailable')
    mp4=tmp_path/'v.mp4';cp=subprocess.run([ffmpeg,'-y','-f','lavfi','-i','color=size=32x32:rate=5:duration=0.4','-c:v','libx264','-pix_fmt','yuv420p',str(mp4)],capture_output=True)
    if cp.returncode:pytest.skip('ffmpeg libx264 unavailable')
    video=mp4.read_bytes();xmp=_MOTION_XMP_LOCAL.format(length=len(video)).encode()
    still=_inject_standard_xmp(_jpeg_with_exif_datetime('2019:06:07 08:09:10'),xmp)
    combined=still+b'GAIN'+video
    got=detect_jpeg_motion_photo(combined);assert got
    d,v=got;assert d.motion_item.length==len(video) and combined[v.start:v.end]==video
    extracted=tmp_path/'extracted.mp4';extracted.write_bytes(combined[v.start:v.end])
    assert has_supported_video_track(probe_video(extracted))

def test_motion_flag_with_stale_length_but_no_real_appended_video_is_rejected():
    xmp=_MOTION_XMP_LOCAL.format(length=999).encode();j=_inject_standard_xmp(_jpeg_with_exif_datetime(),xmp)
    assert descriptor_from_jpeg(j).motion_flag==1
    assert detect_jpeg_motion_photo(j) is None

def test_edited_variant_hint_is_probable_and_does_not_inherit_date(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/foo.jpg':b'ORIGINAL','D/foo-edited.jpg':b'EDITED'})
    assets=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan();edited=[a for a in assets if 'edited' in a.occurrences[0].path][0]
    rel=[r for r in edited.family_relations if r['kind']=='edited-variant-hint'][0]
    assert rel['confidence']=='probable' and edited.date_fact.precision==DatePrecision.UNKNOWN and edited.output_dir.endswith('/Unknown')

def test_localized_edited_suffix_is_detected_as_hint_only(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/photo.jpg':b'A','D/photo-modifié.jpg':b'B','D/bild.jpg':b'C','D/bild-bearbeitet.jpg':b'D'})
    assets=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()
    assert sum(any(r['kind']=='edited-variant-hint' for r in a.family_relations) for a in assets)==4

def test_raw_jpeg_same_stem_is_probable_family_not_dedup(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/IMG_1.dng':b'RAW','D/IMG_1.jpg':b'JPEG'})
    assets=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan();assert len(assets)==2
    assert all(any(r['kind']=='raw-rendered-hint' and r['confidence']=='probable' for r in a.family_relations) for a in assets)
    assert assets[0].sha256!=assets[1].sha256

def test_same_stem_in_different_directories_does_not_create_raw_family_hint(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'A/IMG_1.dng':b'RAW','B/IMG_1.jpg':b'JPEG'})
    assets=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()
    assert not any(r['kind']=='raw-rendered-hint' for a in assets for r in a.family_relations)
