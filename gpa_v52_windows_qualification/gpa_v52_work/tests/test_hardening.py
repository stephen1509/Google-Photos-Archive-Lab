from __future__ import annotations
import json, zipfile
from pathlib import Path
import pytest

from gpa.output import portable_filename, utf16_units, windows_collision_key
from gpa.planner import LibraryPlanner
from gpa.embedded import EmbeddedMetadata


def mkzip(path:Path, entries:dict[str,bytes|str]):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for k,v in entries.items():z.writestr(k,v.encode() if isinstance(v,str) else v)

def gjson(title,ts='1494806400'):
    return json.dumps({'title':title,'photoTakenTime':{'timestamp':ts}})

def test_portable_filename_uses_utf16_units_for_emoji():
    n='📷'*120+'.jpg'
    out=portable_filename(n,max_component=180)
    assert utf16_units(out)<=180
    assert out.endswith('.jpg')

def test_windows_superscript_device_names_are_sanitized():
    assert portable_filename('COM¹.jpg').startswith('_')
    assert portable_filename('LPT²').startswith('_')

def test_windows_collision_key_normalizes_case_and_unicode():
    assert windows_collision_key('É.JPG')==windows_collision_key('E\u0301.jpg')

def test_planner_resolves_same_month_same_name_different_bytes(tmp_path):
    z=tmp_path/'z.zip'
    mkzip(z,{
        'A/IMG.jpg':b'A','A/IMG.jpg.json':gjson('IMG.jpg'),
        'B/IMG.jpg':b'B','B/IMG.jpg.json':gjson('IMG.jpg'),
    })
    assets=LibraryPlanner([z]).plan()
    assert len(assets)==2
    assert assets[0].output_dir==assets[1].output_dir
    assert assets[0].output_name!=assets[1].output_name
    assert all('__' in a.output_name for a in assets)

def test_collision_resolution_is_input_order_independent(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip'
    mkzip(a,{'A/X.jpg':b'A','A/X.jpg.json':gjson('X.jpg')})
    mkzip(b,{'B/X.jpg':b'B','B/X.jpg.json':gjson('X.jpg')})
    x={i.sha256:i.output_name for i in LibraryPlanner([a,b]).plan()}
    y={i.sha256:i.output_name for i in LibraryPlanner([b,a]).plan()}
    assert x==y
import hashlib
from gpa.transactions import ProjectionCopy, ProjectionBytes, apply_projection_update, ProjectionConflict, CommitError, sha256_file
from gpa.revisions import RevisionStore


def test_projection_update_moves_media_and_replaces_xmp_only_after_all_new_files_exist(tmp_path):
    olddir=tmp_path/'Photos/2020/01 - January';newdir=tmp_path/'Photos/2019/12 - December'
    olddir.mkdir(parents=True);media=olddir/'IMG.jpg';xmp=olddir/'IMG.jpg.xmp'
    media.write_bytes(b'MEDIA');xmp.write_bytes(b'OLD-XMP')
    newmedia=newdir/'IMG.jpg';newxmp=newdir/'IMG.jpg.xmp'
    h=hashlib.sha256(b'MEDIA').hexdigest()
    apply_projection_update(
        [ProjectionCopy(media,newmedia,h)],
        [ProjectionBytes(newxmp,b'NEW-XMP')],
        [media,xmp],
    )
    assert newmedia.read_bytes()==b'MEDIA' and newxmp.read_bytes()==b'NEW-XMP'
    assert not media.exists() and not xmp.exists()


def test_projection_update_crash_after_media_commit_leaves_old_projection_and_reruns_safely(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir()
    media=old/'a.jpg';xmp=old/'a.jpg.xmp';media.write_bytes(b'M');xmp.write_bytes(b'OLD')
    h=hashlib.sha256(b'M').hexdigest();newmedia=new/'a.jpg';newxmp=new/'a.jpg.xmp'
    def fail(n,path):
        if n==1:raise RuntimeError('simulated power loss')
    with pytest.raises(RuntimeError):
        apply_projection_update([ProjectionCopy(media,newmedia,h)],[ProjectionBytes(newxmp,b'NEW')],[media,xmp],after_commit=fail)
    # New media may exist, but cleanup cannot have started.
    assert media.exists() and xmp.exists() and newmedia.exists() and not newxmp.exists()
    apply_projection_update([ProjectionCopy(media,newmedia,h)],[ProjectionBytes(newxmp,b'NEW')],[media,xmp])
    assert not media.exists() and not xmp.exists()
    assert newmedia.read_bytes()==b'M' and newxmp.read_bytes()==b'NEW'


def test_projection_update_foreign_destination_blocks_before_cleanup(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    src=old/'a.jpg';src.write_bytes(b'GOOD');dst=new/'a.jpg';dst.write_bytes(b'FOREIGN')
    h=hashlib.sha256(b'GOOD').hexdigest()
    with pytest.raises(ProjectionConflict):apply_projection_update([ProjectionCopy(src,dst,h)],[],[src])
    assert src.read_bytes()==b'GOOD' and dst.read_bytes()==b'FOREIGN'


def test_projection_update_source_hash_mismatch_refuses(tmp_path):
    src=tmp_path/'old.jpg';src.write_bytes(b'changed')
    with pytest.raises(CommitError):apply_projection_update([ProjectionCopy(src,tmp_path/'new.jpg','0'*64)],[],[src])
    assert src.exists() and not (tmp_path/'new.jpg').exists()


def test_incremental_metadata_revision_history_survives_projection_relocation(tmp_path):
    store=RevisionStore(tmp_path/'archive')
    oldrev=store.add_revision('asset',{'date':'2020-01','source':'takeout-1'})
    oldid=store.current('asset')
    newrev=store.add_revision('asset',{'date':'2019-12','source':'takeout-2'})
    newid=store.current('asset')
    assert oldid!=newid and oldrev.exists() and newrev.exists()
    assert len(store.revisions('asset'))==2
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from gpa.model import DateFact, DatePrecision, Confidence, GoogleSidecar
from gpa.writepolicy import plan_metadata
from gpa.xmp import render_xmp_sidecar
from gpa.materializer import ArchiveMaterializer, xmp_path_for, MaterializeError
from gpa.zipindex import ZipLibrary


def test_xmp_partial_month_is_honest_and_description_unicode_roundtrips():
    f=DateFact(DatePrecision.MONTH,Confidence.USER_CONFIRMED,year=2017,month=5,source='user')
    s=GoogleSidecar(description='奈良 & 京都 <trip>',raw={})
    raw=render_xmp_sidecar(plan_metadata(f,s))
    assert raw and b'2017-05' in raw
    root=ET.fromstring(raw)
    text=''.join(root.itertext())
    assert '奈良 & 京都 <trip>' in text
    assert b'2017-05-01' not in raw


def test_xmp_unknown_without_description_is_not_created():
    assert render_xmp_sidecar(plan_metadata(None,None)) is None


def test_materializer_streams_media_unchanged_and_writes_xmp_and_revision(tmp_path):
    z=tmp_path/'t.zip';ts=str(int(datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp()))
    mkzip(z,{'D/IMG.jpg':b'ORIGINAL-BYTES','D/IMG.jpg.json':gjson('IMG.jpg',ts)})
    planner=LibraryPlanner([z]);assets=planner.plan();lib=planner.lib
    root=tmp_path/'archive';out=ArchiveMaterializer(root,lib).materialize(assets[0])
    assert out['media'].read_bytes()==b'ORIGINAL-BYTES'
    assert out['xmp'] and out['xmp'].exists()
    record=json.loads(out['revision'].read_text())
    assert record['media_sha256']==hashlib.sha256(b'ORIGINAL-BYTES').hexdigest()
    assert record['google_raw'][0]['title']=='IMG.jpg'


def test_materializer_is_idempotent_for_same_asset(tmp_path):
    z=tmp_path/'t.zip';mkzip(z,{'D/a.jpg':b'A'})
    planner=LibraryPlanner([z]);a=planner.plan()[0];m=ArchiveMaterializer(tmp_path/'archive',planner.lib)
    x=m.materialize(a);y=m.materialize(a)
    assert x['media']==y['media'] and len(m.revisions.revisions(a.asset_id))==1


def test_materializer_falls_back_to_second_exact_duplicate_source(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip';mkzip(a,{'A/x.jpg':b'SAME'});mkzip(b,{'B/x.jpg':b'SAME'})
    planner=LibraryPlanner([a,b]);asset=planner.plan()[0]
    # Corrupt first archive after planning. Second exact duplicate remains usable.
    a.write_bytes(b'BROKEN')
    out=ArchiveMaterializer(tmp_path/'archive',planner.lib).materialize(asset)
    assert out['media'].read_bytes()==b'SAME'


def test_materializer_never_overwrites_foreign_media(tmp_path):
    z=tmp_path/'t.zip';mkzip(z,{'D/a.jpg':b'A'})
    planner=LibraryPlanner([z]);asset=planner.plan()[0];root=tmp_path/'archive'
    dest=root/asset.output_dir/asset.output_name;dest.parent.mkdir(parents=True);dest.write_bytes(b'FOREIGN')
    with pytest.raises(ProjectionConflict):ArchiveMaterializer(root,planner.lib).materialize(asset)
    assert dest.read_bytes()==b'FOREIGN'


def test_normalized_zip_member_remains_readable(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/x/../photo.jpg':b'P'})
    lib=ZipLibrary([z]);m=lib.scan()[0]
    assert m.path=='D/photo.jpg' and lib.read(m)==b'P'

def album_json(title='Holiday',**extra):
    d={'title':title,'date':{'timestamp':'1'},'access':'protected'};d.update(extra);return json.dumps(d)


def test_album_metadata_filename_can_be_localized(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'Google Fotos/Urlaub/IMG.jpg':b'A','Google Fotos/Urlaub/Metadaten.json':album_json('Urlaub')})
    a=LibraryPlanner([z]).plan()[0]
    assert len(a.albums)==1 and a.albums[0].title=='Urlaub'


def test_album_metadata_and_media_can_be_in_different_zip_parts(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip'
    mkzip(a,{'Takeout/Google Photos/Trip/metadata.json':album_json('Trip')})
    mkzip(b,{'Takeout/Google Photos/Trip/IMG.jpg':b'A'})
    x=LibraryPlanner([a,b]).plan()[0]
    assert [q.title for q in x.albums]==['Trip']


def test_exact_duplicate_retains_all_album_memberships(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip'
    mkzip(a,{'GP/Trip A/x.jpg':b'SAME','GP/Trip A/metadata.json':album_json('Trip A')})
    mkzip(b,{'GP/Trip B/x.jpg':b'SAME','GP/Trip B/métadonnées.json':album_json('Trip B')})
    x=LibraryPlanner([a,b]).plan()[0]
    assert {q.title for q in x.albums}=={'Trip A','Trip B'}


def test_untitled_album_is_preserved_without_invented_title(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'GP/Untitled/x.jpg':b'A','GP/Untitled/metadata.json':album_json('')})
    x=LibraryPlanner([z]).plan()[0]
    assert len(x.albums)==1 and x.albums[0].title is None
    assert any('untitled' in w for w in x.albums[0].warnings)


def test_conflicting_album_titles_same_logical_folder_are_not_chosen(tmp_path):
    a=tmp_path/'a.zip';b=tmp_path/'b.zip'
    mkzip(a,{'GP/Trip/metadata.json':album_json('Trip')})
    mkzip(b,{'GP/Trip/x.jpg':b'A','GP/Trip/Metadaten.json':album_json('Different')})
    x=LibraryPlanner([a,b]).plan()[0]
    assert x.albums[0].title is None and x.albums[0].warnings


def test_album_location_stays_context_not_photo_gps(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'GP/Trip/x.jpg':b'A','GP/Trip/metadata.json':album_json('Trip',location={'latitude':34.6,'longitude':135.8})})
    planner=LibraryPlanner([z]);x=planner.plan()[0]
    out=ArchiveMaterializer(tmp_path/'archive',planner.lib).materialize(x)
    rec=json.loads(out['revision'].read_text())
    assert rec['albums'][0]['raw_records'][0]['location']['latitude']==34.6
    assert out['xmp'] is None  # album GPS is never promoted to per-photo XMP
from gpa.executor import RunExecutor, RunError


def test_tolerant_plan_keeps_good_zip_when_another_zip_is_invalid(tmp_path):
    good=tmp_path/'good.zip';bad=tmp_path/'bad.zip';mkzip(good,{'D/a.jpg':b'A'});bad.write_bytes(b'not zip')
    r=LibraryPlanner([bad,good]).plan_report()
    assert len(r.assets)==1 and any(p.kind=='invalid_zip' for p in r.source_problems)


def test_malformed_json_does_not_abort_healthy_media_but_is_unclassified(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':'{bad json'})
    r=LibraryPlanner([z]).plan_report()
    assert len(r.assets)==1 and r.assets[0].date_fact.precision==DatePrecision.UNKNOWN
    assert any(p.kind=='invalid_json' for p in r.source_problems)
    assert any(x.path.endswith('.json') for x in r.unclassified)


def test_unknown_takeout_member_is_preserved_and_run_needs_review(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A','D/future-format.xyz':b'FUTURE'})
    ex=RunExecutor([z],tmp_path/'archive');r=ex.plan();m=ex.execute(r,run_id='run1')
    assert m['status']=='needs_review'
    saved=m['unclassified_preserved'];assert len(saved)==1
    p=tmp_path/'archive'/saved[0]['saved_as'];assert p.read_bytes()==b'FUTURE'


def test_run_manifest_fingerprints_source_zip(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A'})
    ex=RunExecutor([z],tmp_path/'archive');m=ex.execute(ex.plan(),run_id='run1')
    assert m['source_archives'][0]['sha256']==hashlib.sha256(z.read_bytes()).hexdigest()
    assert Path(m['manifest_path']).exists()


def test_run_refuses_archive_changed_after_planning(tmp_path):
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A'})
    ex=RunExecutor([z],tmp_path/'archive');r=ex.plan()
    mkzip(z,{'D/a.jpg':b'B'})
    with pytest.raises(RunError):ex.execute(r,run_id='run1')


def test_run_with_invalid_zip_is_partial_but_good_asset_commits(tmp_path):
    good=tmp_path/'good.zip';bad=tmp_path/'bad.zip';mkzip(good,{'D/a.jpg':b'A'});bad.write_bytes(b'broken')
    ex=RunExecutor([bad,good],tmp_path/'archive');m=ex.execute(ex.plan(),run_id='run1')
    assert m['status']=='partial' and m['assets_committed']==1 and m['source_problems']
from gpa.location import LocationPolicy, resolve_location
from gpa.timezones import TimezoneEvidence, resolve_with_timezone_evidence
from gpa.review import user_month_range
from gpa.output import placement_dir


def test_location_default_preserves_both_and_uses_google_current():
    s=GoogleSidecar(geo_current=(34.6,135.8),geo_exif=(35.0,136.0))
    f=resolve_location([s])
    assert (f.lat,f.lon)==(34.6,135.8) and f.source=='google-current'
    assert f.original_candidates==[(35.0,136.0)] and f.warnings


def test_location_strict_policy_refuses_conflicting_current_and_original():
    s=GoogleSidecar(geo_current=(34.6,135.8),geo_exif=(35.0,136.0))
    f=resolve_location([s],LocationPolicy.REQUIRE_AGREEMENT)
    assert f.confidence==Confidence.CONFLICT and f.lat is None and f.lon is None


def test_location_strict_accepts_single_available_source():
    s=GoogleSidecar(geo_current=(34.6,135.8))
    f=resolve_location([s],LocationPolicy.REQUIRE_AGREEMENT)
    assert f.exact and f.source=='google-current-only'


def test_duplicate_location_conflict_never_silently_picks_first():
    a=GoogleSidecar(geo_current=(1.0,2.0));b=GoogleSidecar(geo_current=(3.0,4.0))
    f=resolve_location([a,b])
    assert f.confidence==Confidence.CONFLICT and f.lat is None


def test_current_timezone_boundary_is_only_suggestion_not_archival_exact():
    s=GoogleSidecar(taken_epoch=int(datetime(2017,5,1,1,tzinfo=timezone.utc).timestamp()))
    ev=TimezoneEvidence('Asia/Tokyo',Confidence.PROBABLE,'current-boundaries')
    f,suggestion=resolve_with_timezone_evidence(s,ev)
    assert suggestion.hour==10
    assert f.precision==DatePrecision.RANGE  # still ambiguous without proven historical boundary
    assert 'Probable local time' in f.note


def test_historically_proven_timezone_can_resolve_exact_local_time():
    s=GoogleSidecar(taken_epoch=int(datetime(2017,4,30,16,tzinfo=timezone.utc).timestamp()))
    ev=TimezoneEvidence('Asia/Tokyo',Confidence.PROVEN,'same-since-1970')
    f,_=resolve_with_timezone_evidence(s,ev)
    assert f.precision==DatePrecision.INSTANT and (f.year,f.month,f.value.day)==(2017,5,1)
    assert 'same-since-1970' in f.source


def test_user_april_or_may_range_never_becomes_final_month():
    f=user_month_range(2017,4,2017,5)
    assert f.confidence==Confidence.USER_CONFIRMED and f.precision==DatePrecision.RANGE
    assert placement_dir(f).startswith('_Needs Placement/Date/2017-04-01_to_2017-05-31')
    p=plan_metadata(f,None)
    assert 'DateTimeOriginal' not in p.exif and 'XMP-photoshop:DateCreated' not in p.xmp


def test_materializer_xmp_uses_effective_gps_policy_but_record_keeps_both(tmp_path):
    z=tmp_path/'z.zip';ts=str(int(datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp()))
    side=json.dumps({'title':'x.jpg','photoTakenTime':{'timestamp':ts},'geoData':{'latitude':34.6,'longitude':135.8},'geoDataExif':{'latitude':35.0,'longitude':136.0}})
    mkzip(z,{'D/x.jpg':b'X','D/x.jpg.json':side})
    planner=LibraryPlanner([z]);a=planner.plan()[0]
    out=ArchiveMaterializer(tmp_path/'archive',planner.lib).materialize(a)
    rec=json.loads(out['revision'].read_text())
    assert rec['location']['current_candidates']==[[34.6,135.8]]
    assert rec['location']['original_candidates']==[[35.0,136.0]]
    assert rec['location']['source']=='google-current'
from gpa.motion import parse_motion_xmp, locate_jpeg_from_descriptor, validate_heif_descriptor



def _box(typ:bytes,payload:bytes)->bytes:
    return (len(payload)+8).to_bytes(4,'big')+typ+payload

def _jpeg_with_app1(meta=b'OLD',scan=b'\x01\x02\x03'):
    app=b'\xff\xe1'+(len(meta)+2).to_bytes(2,'big')+meta
    dqt=b'\xff\xdb\x00\x04\x00\x01'
    sos=b'\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00'
    return b'\xff\xd8'+app+dqt+sos+scan+b'\xff\xd9'

MOTION_XMP='''<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:Camera="http://ns.google.com/photos/1.0/camera/" xmlns:Container="http://ns.google.com/photos/1.0/container/" xmlns:Item="http://ns.google.com/photos/1.0/container/item/">
<rdf:RDF><rdf:Description Camera:MotionPhoto="1" Camera:MotionPhotoVersion="1" Camera:MotionPhotoPresentationTimestampUs="123">
<Container:Directory><rdf:Seq>
<rdf:li rdf:parseType="Resource" Item:Mime="image/jpeg" Item:Semantic="Primary" Item:Padding="0"/>
<rdf:li rdf:parseType="Resource" Item:Mime="image/jpeg" Item:Semantic="GainMap" Item:Length="4"/>
<rdf:li rdf:parseType="Resource" Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="{length}"/>
</rdf:Seq></Container:Directory></rdf:Description></rdf:RDF></x:xmpmeta>'''


def test_motion_xmp_parser_understands_ultra_hdr_item_order():
    d=parse_motion_xmp(MOTION_XMP.format(length=100))
    assert d.structurally_declared and d.motion_item.length==100
    assert [x.semantic for x in d.items]==['Primary','GainMap','MotionPhoto']


def test_motion_xmp_flag_without_container_is_not_structurally_declared():
    d=parse_motion_xmp('<x xmlns:Camera="http://ns.google.com/photos/1.0/camera/" Camera:MotionPhoto="1"/>')
    assert not d.structurally_declared and d.warnings


def test_motion_xmp_flag_zero_refuses_real_appended_video():
    video=_box(b'ftyp',b'isom')+_box(b'mdat',b'abc');still=_jpeg_with_app1()
    x=MOTION_XMP.format(length=len(video)).replace('MotionPhoto="1"','MotionPhoto="0"')
    assert locate_jpeg_from_descriptor(still+video,parse_motion_xmp(x)) is None


def test_motion_xmp_declared_length_locates_video_after_gainmap_bytes():
    video=_box(b'ftyp',b'isom')+_box(b'moov',b'x')+_box(b'mdat',b'abc');still=_jpeg_with_app1()
    data=still+b'GAINMAP'+video;d=parse_motion_xmp(MOTION_XMP.format(length=len(video)))
    got=locate_jpeg_from_descriptor(data,d)
    assert got and data[got.start:got.end]==video


def test_motion_xmp_wrong_length_fails_structural_video_check():
    video=_box(b'ftyp',b'isom')+_box(b'mdat',b'abc');still=_jpeg_with_app1()
    d=parse_motion_xmp(MOTION_XMP.format(length=len(video)-1))
    assert locate_jpeg_from_descriptor(still+video,d) is None


def test_legacy_microvideo_offset_requires_explicit_compatibility_and_real_video():
    video=_box(b'ftyp',b'isom')+_box(b'mdat',b'abc');still=_jpeg_with_app1()
    x=f'<x xmlns:Camera="http://ns.google.com/photos/1.0/camera/" Camera:MotionPhoto="1" Camera:MicroVideoOffset="{len(video)}"/>'
    d=parse_motion_xmp(x)
    assert locate_jpeg_from_descriptor(still+video,d) is None
    assert locate_jpeg_from_descriptor(still+video,d,allow_legacy_microvideo=True)


def test_heif_motion_descriptor_requires_padding_8_and_matching_mpvd_length():
    video=_box(b'ftyp',b'isom')+_box(b'moov',b'x')+_box(b'mdat',b'abc')
    data=_box(b'ftyp',b'heic')+_box(b'meta',b'x')+_box(b'mpvd',video)
    x=MOTION_XMP.format(length=len(video)).replace('image/jpeg" Item:Semantic="Primary" Item:Padding="0"','image/heic" Item:Semantic="Primary" Item:Padding="8"')
    assert validate_heif_descriptor(data,parse_motion_xmp(x))
    bad=x.replace('Padding="8"','Padding="0"')
    assert validate_heif_descriptor(data,parse_motion_xmp(bad)) is None
from gpa.families import EmbeddedEvidence, content_identifier_from_exiftool, match_live_photos, apply_live_photo_placement


def test_exiftool_live_photo_content_ids_use_grouped_tags():
    assert content_identifier_from_exiftool({'Apple:ContentIdentifier':'ABC'},'still')=='ABC'
    assert content_identifier_from_exiftool({'Keys:ContentIdentifier':'ABC'},'movie')=='ABC'
    assert content_identifier_from_exiftool({'Random:ContentIdentifier':'ABC'},'movie') is None


def test_live_photo_pairing_uses_identifier_not_filename():
    rel=match_live_photos([EmbeddedEvidence('s','totally-renamed.heic','ID1'),EmbeddedEvidence('m','different-name.mov','ID1')])
    assert len(rel)==1 and rel[0].confidence==Confidence.PROVEN and rel[0].member_ids==['s','m']


def test_same_basename_without_identifier_produces_no_live_relation():
    assert match_live_photos([EmbeddedEvidence('s','IMG_1.heic'),EmbeddedEvidence('m','IMG_1.mov')])==[]


def test_duplicate_content_identifier_is_conflict_not_arbitrary_pair():
    rel=match_live_photos([EmbeddedEvidence('s1','a.heic','ID'),EmbeddedEvidence('s2','b.heic','ID'),EmbeddedEvidence('m','c.mov','ID')])[0]
    assert rel.confidence==Confidence.CONFLICT


def _fake_asset(asset_id,filename,date_fact):
    from gpa.planner import LogicalAsset
    a=LogicalAsset(asset_id,asset_id);a.output_name=filename;a.date_fact=date_fact;a.placement_fact=date_fact;a.output_dir=placement_dir(date_fact);return a


def test_live_movie_inherits_folder_month_but_not_capture_metadata():
    still=_fake_asset('s','renamed.heic',DateFact(DatePrecision.MONTH,Confidence.PROVEN,year=2017,month=5,source='still'))
    movie=_fake_asset('m','other.mov',DateFact(DatePrecision.UNKNOWN,Confidence.UNKNOWN,source='none'))
    rel=match_live_photos([EmbeddedEvidence('s','renamed.heic','ID'),EmbeddedEvidence('m','other.mov','ID')])[0]
    apply_live_photo_placement({'s':still,'m':movie},rel)
    assert movie.output_dir=='Photos/2017/05 - May'
    assert movie.placement_fact.source=='live-photo-companion-placement'
    assert movie.date_fact.precision==DatePrecision.UNKNOWN  # no fake movie DateTimeOriginal


def test_live_components_disagreeing_on_month_go_to_review_together():
    a=_fake_asset('s','a.heic',DateFact(DatePrecision.MONTH,Confidence.PROVEN,year=2017,month=5))
    b=_fake_asset('m','b.mov',DateFact(DatePrecision.MONTH,Confidence.PROVEN,year=2017,month=6))
    rel=match_live_photos([EmbeddedEvidence('s','a.heic','ID'),EmbeddedEvidence('m','b.mov','ID')])[0]
    apply_live_photo_placement({'s':a,'m':b},rel)
    assert a.placement_fact.confidence==Confidence.CONFLICT and b.placement_fact.confidence==Confidence.CONFLICT
    assert a.output_dir.endswith('/Unknown') and b.output_dir.endswith('/Unknown')
import stat
from gpa.storage import windows_path_risk, preflight_destination
from gpa.zipindex import ArchiveSafetyError, SourceIntegrityError


def test_windows_path_risk_counts_emoji_as_two_utf16_units():
    p='Photos/'+'📷'*130+'.jpg'
    assert any('UTF-16' in x for x in windows_path_risk(p,component_limit=255,conservative_limit=1000))


def test_storage_preflight_includes_metadata_overhead(tmp_path):
    p=preflight_destination(tmp_path,[100,200,300],metadata_overhead_bytes=50,reserve_fraction=0)
    assert p.required_bytes==950


def test_zip_symlink_member_is_rejected(tmp_path):
    z=tmp_path/'z.zip'
    with zipfile.ZipFile(z,'w') as f:
        i=zipfile.ZipInfo('link.jpg');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16
        f.writestr(i,'target.jpg')
    with pytest.raises(ArchiveSafetyError):ZipLibrary([z]).scan()


def test_tolerant_scan_reports_symlink_but_keeps_good_member(tmp_path):
    z=tmp_path/'z.zip'
    with zipfile.ZipFile(z,'w') as f:
        f.writestr('good.jpg',b'G')
        i=zipfile.ZipInfo('link.jpg');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16;f.writestr(i,'target')
    lib=ZipLibrary([z]);members,problems=lib.scan_tolerant()
    assert [m.path for m in members]==['good.jpg'] and any(p.kind=='archive_safety' for p in problems)


def test_normalized_duplicate_member_paths_are_rejected(tmp_path):
    z=tmp_path/'z.zip'
    with zipfile.ZipFile(z,'w') as f:
        f.writestr('D/x/../a.jpg',b'A');f.writestr('D/a.jpg',b'B')
    with pytest.raises(ArchiveSafetyError):ZipLibrary([z]).scan()


def test_crc_corruption_fails_cleanly_and_leaves_no_stage(tmp_path):
    z=tmp_path/'z.zip'
    with zipfile.ZipFile(z,'w',compression=zipfile.ZIP_STORED) as f:f.writestr('a.jpg',b'abcdef')
    raw=bytearray(z.read_bytes());name_len=int.from_bytes(raw[26:28],'little');extra_len=int.from_bytes(raw[28:30],'little');start=30+name_len+extra_len
    raw[start]^=0x01;z.write_bytes(raw)
    lib=ZipLibrary([z]);m=lib.scan()[0];stage=tmp_path/'stage';stage.mkdir()
    with pytest.raises(SourceIntegrityError):lib.extract_to_staging(m,stage)
    assert not list(stage.glob('.gpa-stage-*'))
from gpa.catalog import Catalog, date_interval, rebuild_catalog_from_archive


def test_catalog_interval_search_handles_month_without_fake_day(tmp_path):
    c=Catalog(tmp_path/'c.db');start,end,y,m=date_interval({'precision':'month','year':2017,'month':5})
    c.add(id='a',path='a.jpg',capture_start=start,capture_end=end,year=y,month=m,needs_review=0)
    assert c.between('2017-05-15T00:00:00','2017-05-15T23:59:59')==[('a','a.jpg')]
    assert c.between('2017-06-01T00:00:00','2017-06-30T23:59:59')==[]


def test_catalog_text_search_includes_tags_description_and_place(tmp_path):
    c=Catalog(tmp_path/'c.db');c.add(id='a',path='a.jpg',description='Cherry blossoms',place='Nara',needs_review=0);c.tag('a','album','Japan 2017');c.tag('a','person','Alice')
    assert c.search_text('cherry')==[('a','a.jpg')]
    assert c.search_text('nara')==[('a','a.jpg')]
    assert c.search_text('alice')==[('a','a.jpg')]


def test_rebuild_catalog_uses_open_revision_records_not_sqlite_as_authority(tmp_path):
    z=tmp_path/'z.zip';ts=str(int(datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp()))
    side=json.dumps({'title':'x.jpg','description':'Nara trip','photoTakenTime':{'timestamp':ts},'people':[{'name':'Alice'}],'geoData':{'latitude':34.6851,'longitude':135.8048}})
    mkzip(z,{'Trip/x.jpg':b'X','Trip/x.jpg.json':side,'Trip/metadata.json':album_json('Japan 2017')})
    planner=LibraryPlanner([z]);a=planner.plan()[0];root=tmp_path/'archive';ArchiveMaterializer(root,planner.lib).materialize(a)
    db=root/'catalog.sqlite';c=rebuild_catalog_from_archive(root,db)
    assert c.by_month(2017,5)
    assert c.tagged('album','Japan 2017')
    assert c.tagged('person','Alice')
    assert c.near(34.6851,135.8048,1)[0][0]==a.asset_id
    c.close();db.unlink();c2=rebuild_catalog_from_archive(root,db)
    assert c2.by_month(2017,5)  # disposable database rebuilt from archive records
from gpa.incremental import IncrementalUpdater, IncrementalError


def _takeout_sidecar(title,dt,description=''):
    ts=str(int(dt.replace(tzinfo=timezone.utc).timestamp()))
    return json.dumps({'title':title,'photoTakenTime':{'timestamp':ts},'description':description})


def test_incremental_same_bytes_corrected_month_relocates_and_keeps_old_revision(tmp_path):
    z1=tmp_path/'one.zip';z2=tmp_path/'two.zip';media=b'IRREPLACEABLE-MEDIA'
    mkzip(z1,{'D/IMG.jpg':media,'D/IMG.jpg.json':_takeout_sidecar('IMG.jpg',datetime(2020,1,15,12),'old')})
    p1=LibraryPlanner([z1]);a1=p1.plan()[0];root=tmp_path/'archive';m1=ArchiveMaterializer(root,p1.lib)
    first=m1.materialize(a1);old_media=first['media'];old_xmp=first['xmp']
    old_revision=RevisionStore(root).current(a1.asset_id)

    mkzip(z2,{'D/IMG.jpg':media,'D/IMG.jpg.json':_takeout_sidecar('IMG.jpg',datetime(2019,12,15,12),'corrected')})
    p2=LibraryPlanner([z2]);a2=p2.plan()[0];up=IncrementalUpdater(root,ArchiveMaterializer(root,p2.lib))
    got=up.apply(a2)

    assert got.action=='relocated'
    assert got.media.read_bytes()==media and '2019/12 - December' in got.media.as_posix()
    assert not old_media.exists() and (not old_xmp or not old_xmp.exists())
    store=RevisionStore(root);assert store.current(a2.asset_id)!=old_revision
    assert len(store.revisions(a2.asset_id))==2
    assert list((root/'metadata/assets'/a2.asset_id/'blobs/xmp').glob('*.xmp'))


def test_incremental_same_path_metadata_update_replaces_current_xmp_but_archives_old_bytes(tmp_path):
    z1=tmp_path/'one.zip';z2=tmp_path/'two.zip';media=b'MEDIA'
    dt=datetime(2020,1,15,12)
    mkzip(z1,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'first caption')})
    p1=LibraryPlanner([z1]);a1=p1.plan()[0];root=tmp_path/'archive';first=ArchiveMaterializer(root,p1.lib).materialize(a1)
    old_xmp=first['xmp'].read_bytes();media_sha=sha256_file(first['media'])
    mkzip(z2,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'second caption')})
    p2=LibraryPlanner([z2]);a2=p2.plan()[0]
    got=IncrementalUpdater(root,ArchiveMaterializer(root,p2.lib)).apply(a2)
    assert got.action=='metadata_update' and got.media==first['media']
    assert sha256_file(got.media)==media_sha
    assert b'second caption' in got.xmp.read_bytes() and got.xmp.read_bytes()!=old_xmp
    blobs=list((root/'metadata/assets'/a2.asset_id/'blobs/xmp').glob('*.xmp'))
    assert any(p.read_bytes()==old_xmp for p in blobs)
    assert len(RevisionStore(root).revisions(a2.asset_id))==2


def test_incremental_crash_after_relocation_before_current_pointer_is_rerunnable(tmp_path):
    z1=tmp_path/'one.zip';z2=tmp_path/'two.zip';media=b'MEDIA'
    mkzip(z1,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2020,1,15,12),'old')})
    p1=LibraryPlanner([z1]);a1=p1.plan()[0];root=tmp_path/'archive';first=ArchiveMaterializer(root,p1.lib).materialize(a1)
    old_rid=RevisionStore(root).current(a1.asset_id)
    mkzip(z2,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2019,12,15,12),'new')})
    p2=LibraryPlanner([z2]);a2=p2.plan()[0];up=IncrementalUpdater(root,ArchiveMaterializer(root,p2.lib))
    with pytest.raises(RuntimeError):up.apply(a2,after_projection=lambda:(_ for _ in ()).throw(RuntimeError('power loss')))
    # Projection may already be fully relocated, but old metadata pointer remains authoritative.
    assert RevisionStore(root).current(a1.asset_id)==old_rid
    got=up.apply(a2)
    assert got.action=='relocated' and got.media.exists()
    assert RevisionStore(root).current(a1.asset_id)!=old_rid


def test_incremental_foreign_destination_refuses_without_destroying_current_projection(tmp_path):
    z1=tmp_path/'one.zip';z2=tmp_path/'two.zip';media=b'MEDIA'
    mkzip(z1,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2020,1,15,12),'old')})
    p1=LibraryPlanner([z1]);a1=p1.plan()[0];root=tmp_path/'archive';first=ArchiveMaterializer(root,p1.lib).materialize(a1)
    mkzip(z2,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2019,12,15,12),'new')})
    p2=LibraryPlanner([z2]);a2=p2.plan()[0]
    foreign=root/a2.output_dir/a2.output_name;foreign.parent.mkdir(parents=True,exist_ok=True);foreign.write_bytes(b'FOREIGN')
    with pytest.raises(ProjectionConflict):IncrementalUpdater(root,ArchiveMaterializer(root,p2.lib)).apply(a2)
    assert first['media'].read_bytes()==media and foreign.read_bytes()==b'FOREIGN'


def test_incremental_identical_state_is_history_noop(tmp_path):
    z=tmp_path/'z.zip';media=b'MEDIA';dt=datetime(2020,1,15,12)
    mkzip(z,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'same')})
    p=LibraryPlanner([z]);a=p.plan()[0];root=tmp_path/'archive';mat=ArchiveMaterializer(root,p.lib);mat.materialize(a)
    before=RevisionStore(root).revisions(a.asset_id)
    got=IncrementalUpdater(root,mat).apply(a)
    assert got.action=='unchanged' and RevisionStore(root).revisions(a.asset_id)==before


def test_run_executor_automatically_applies_incremental_metadata_relocation(tmp_path):
    z1=tmp_path/'one.zip';z2=tmp_path/'two.zip';root=tmp_path/'archive';media=b'MEDIA'
    mkzip(z1,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2020,1,15,12),'old')})
    first=RunExecutor([z1],root).execute(run_id='run1')
    assert first['status']=='complete' and first['successes'][0]['action']=='new'
    old_path=root/first['successes'][0]['media'];assert old_path.exists()

    mkzip(z2,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2019,12,15,12),'corrected')})
    second=RunExecutor([z2],root).execute(run_id='run2')
    assert second['status']=='complete' and second['successes'][0]['action']=='relocated'
    new_path=root/second['successes'][0]['media']
    assert new_path.exists() and new_path.read_bytes()==media and not old_path.exists()
    asset_id=second['successes'][0]['asset_id']
    assert len(RevisionStore(root).revisions(asset_id))==2


def test_run_executor_reimport_identical_state_is_unchanged(tmp_path):
    z=tmp_path/'z.zip';root=tmp_path/'archive';media=b'MEDIA'
    mkzip(z,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2020,1,15,12),'same')})
    one=RunExecutor([z],root).execute(run_id='run1');two=RunExecutor([z],root).execute(run_id='run2')
    assert one['successes'][0]['action']=='new' and two['successes'][0]['action']=='unchanged'
    aid=one['successes'][0]['asset_id'];assert len(RevisionStore(root).revisions(aid))==1


def test_portable_xmp_carries_exact_gps_in_exif_xmp_coordinate_format():
    p=plan_metadata(None,None,exact_gps=(34.6851,135.8048))
    raw=render_xmp_sidecar(p);assert raw
    root=ET.fromstring(raw)
    desc=root.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
    assert desc.attrib['{http://ns.adobe.com/exif/1.0/}GPSLatitude'].startswith('34,41.106')
    assert desc.attrib['{http://ns.adobe.com/exif/1.0/}GPSLatitude'].endswith('N')
    assert desc.attrib['{http://ns.adobe.com/exif/1.0/}GPSLongitude'].endswith('E')


def test_portable_xmp_gps_preserves_southern_western_hemispheres():
    raw=render_xmp_sidecar(plan_metadata(None,None,exact_gps=(-36.8485,-174.7633)))
    root=ET.fromstring(raw);d=root.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
    assert d.attrib['{http://ns.adobe.com/exif/1.0/}GPSLatitude'].endswith('S')
    assert d.attrib['{http://ns.adobe.com/exif/1.0/}GPSLongitude'].endswith('W')


def test_text_location_without_exact_gps_still_does_not_create_xmp_coordinates():
    p=plan_metadata(None,None,location_label='Nara, Japan')
    assert 'XMP-exif:GPSLatitude' not in p.xmp and render_xmp_sidecar(p) is None
from gpa.audit import audit_archive


def test_archive_audit_verifies_media_xmp_revisions_and_blobs(tmp_path):
    z1=tmp_path/'one.zip';z2=tmp_path/'two.zip';root=tmp_path/'archive';media=b'MEDIA'
    dt=datetime(2020,1,15,12)
    mkzip(z1,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'old')})
    RunExecutor([z1],root).execute(run_id='run1')
    mkzip(z2,{'D/a.jpg':media,'D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'new')})
    RunExecutor([z2],root).execute(run_id='run2')
    report=audit_archive(root)
    assert report.ok and report.assets_checked==1 and report.revisions_checked==2 and report.blobs_checked==1


def test_archive_audit_detects_current_media_corruption(tmp_path):
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    m=RunExecutor([z],root).execute(run_id='r');p=root/m['successes'][0]['media'];p.write_bytes(b'CORRUPT')
    r=audit_archive(root)
    assert not r.ok and any(x.kind=='media_hash_mismatch' for x in r.problems)


def test_archive_audit_detects_current_xmp_corruption(tmp_path):
    z=tmp_path/'z.zip';root=tmp_path/'archive'
    mkzip(z,{'D/a.jpg':b'MEDIA','D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2020,1,15,12),'caption')})
    m=RunExecutor([z],root).execute(run_id='r');xp=root/m['successes'][0]['xmp'];xp.write_bytes(b'<broken')
    r=audit_archive(root)
    assert any(x.kind=='xmp_hash_mismatch' for x in r.problems) and any(x.kind=='invalid_xmp' for x in r.problems)


def test_archive_audit_detects_tampered_immutable_revision(tmp_path):
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    m=RunExecutor([z],root).execute(run_id='r');aid=m['successes'][0]['asset_id'];rev=RevisionStore(root).revisions(aid)[0]
    obj=json.loads(rev.read_text());obj['warnings']=['tampered'];rev.write_text(json.dumps(obj))
    r=audit_archive(root);assert any(x.kind=='revision_hash_mismatch' for x in r.problems)


def test_archive_audit_verifies_preserved_unknown_material(tmp_path):
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA','D/future.xyz':b'UNKNOWN'})
    m=RunExecutor([z],root).execute(run_id='r');saved=root/m['unclassified_preserved'][0]['saved_as'];saved.write_bytes(b'CHANGED')
    r=audit_archive(root);assert any(x.kind=='unclassified_hash_mismatch' for x in r.problems)

def test_planner_uses_batch_embedded_provider_when_available(tmp_path):
    class BatchOnly:
        def __init__(self):self.calls=0;self.seen=[]
        def batch(self,members,lib):
            self.calls+=1;self.seen=[m.path for m in members]
            return {(m.archive,m.path):EmbeddedMetadata(capture_local=datetime(2017,5,10,12)) for m in members}
        def __call__(self,m,lib):
            raise AssertionError('planner should not call per-member provider when batch supplied a result')
    z=tmp_path/'z.zip';mkzip(z,{f'D/p{i}.jpg':f'unique-{i}'.encode() for i in range(25)})
    provider=BatchOnly();assets=LibraryPlanner([z],embedded_provider=provider).plan()
    assert provider.calls==1 and len(provider.seen)==25 and len(assets)==25
    assert all(a.output_dir=='Photos/2017/05 - May' for a in assets)

def test_geonames_human_admin_and_country_labels_are_optional_enrichment(tmp_path):
    from gpa.gazetteer import GeoNamesGazetteer
    row=['1','Nara','Nara','','34.6851','135.8048','P','PPL','JP','','29','','','','1000','0','','','2026-01-01']
    cities=tmp_path/'cities.txt';cities.write_text('\t'.join(row)+'\n',encoding='utf-8')
    countries=tmp_path/'countryInfo.txt';countries.write_text('JP\tJPN\t392\tJA\tJapan\tTokyo\t0\t0\tAS\t.jp\tJPY\tYen\t81\t\t\tja\t1861060\t\t\n',encoding='utf-8')
    admin=tmp_path/'admin1.txt';admin.write_text('JP.29\tNara\tNara\t1855612\n',encoding='utf-8')
    g=GeoNamesGazetteer(tmp_path/'g.db','GeoNames synthetic');g.import_geonames_tsv(cities);g.import_country_info(countries);g.import_admin1_codes(admin)
    p=g.nearest(34.6851,135.8048,5);g.close()
    assert p.label=='Nara, Japan' and p.country_name=='Japan' and p.admin1_name=='Nara'
    assert p.confidence=='derived' and 'GPS remains authoritative' in p.note


def test_planner_place_enrichment_requires_exact_gps_and_preserves_provenance(tmp_path):
    from gpa.gazetteer import PlaceEvidence
    class Resolver:
        def __init__(self):self.calls=[]
        def nearest(self,lat,lon):
            self.calls.append((lat,lon));return PlaceEvidence('Nara, Japan','Nara','JP','29',0.3,1,'GeoNames 2026','Japan','Nara')
    r=Resolver();z=tmp_path/'z.zip'
    side=json.dumps({'title':'a.jpg','geoData':{'latitude':34.6851,'longitude':135.8048}})
    mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':side,'D/b.jpg':b'B'})
    assets=LibraryPlanner([z],embedded_provider=lambda m,l: None,place_resolver=r).plan();by={Path(a.occurrences[0].path).name:a for a in assets}
    assert r.calls==[(34.6851,135.8048)]
    assert by['a.jpg'].place_evidence.label=='Nara, Japan' and by['a.jpg'].location_fact.lat==34.6851
    assert by['b.jpg'].place_evidence is None


def test_place_enrichment_survives_open_record_and_rebuilds_searchable_catalog(tmp_path):
    from gpa.gazetteer import PlaceEvidence
    class Resolver:
        def nearest(self,lat,lon):return PlaceEvidence('Nara, Japan','Nara','JP','29',0.1,1,'GeoNames synthetic','Japan','Nara')
    z=tmp_path/'z.zip';root=tmp_path/'archive'
    side=json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':str(int(datetime(2017,5,15,12,tzinfo=timezone.utc).timestamp()))},'geoData':{'latitude':34.6851,'longitude':135.8048}})
    mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':side})
    m=RunExecutor([z],root,place_resolver=Resolver(),embedded_provider=lambda m,l: None).execute(run_id='r')
    assert m['status']=='complete'
    aid=m['successes'][0]['asset_id'];rev=RevisionStore(root).revisions(aid)[0];rec=json.loads(rev.read_text())
    assert rec['place']['label']=='Nara, Japan' and rec['place']['dataset']=='GeoNames synthetic'
    c=rebuild_catalog_from_archive(root,root/'catalog.sqlite')
    assert c.by_place('Nara, Japan')==[(aid,m['successes'][0]['media'])]
    assert c.tagged('country','Japan') and c.tagged('admin1','Nara');c.close()


def test_planner_proven_historical_timezone_upgrades_boundary_timestamp_to_exact_local_month(tmp_path):
    from gpa.timezones import TimezoneEvidence
    class TZ:
        def evidence_at(self,lat,lon,capture_year):
            assert (lat,lon)==(34.6851,135.8048) and capture_year==2017
            return TimezoneEvidence('Asia/Tokyo',Confidence.PROVEN,'TBB:2026c:since1970','historically valid','2026a')
    epoch=int(datetime(2017,4,30,16,0,tzinfo=timezone.utc).timestamp())  # May 1 in Japan
    side=json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':str(epoch)},'geoData':{'latitude':34.6851,'longitude':135.8048}})
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':side})
    a=LibraryPlanner([z],embedded_provider=lambda m,l: None,timezone_resolver=TZ()).plan()[0]
    assert a.date_fact.precision==DatePrecision.INSTANT and a.date_fact.value.isoformat().startswith('2017-05-01T01:00:00+09:00')
    assert a.output_dir=='Photos/2017/05 - May' and a.timezone_evidence.dataset=='TBB:2026c:since1970'


def test_planner_probable_timezone_is_suggestion_only_and_does_not_choose_boundary_month(tmp_path):
    from gpa.timezones import TimezoneEvidence
    class TZ:
        def evidence_at(self,lat,lon,capture_year):return TimezoneEvidence('Asia/Tokyo',Confidence.PROBABLE,'current-boundaries','not historical','2026a')
    epoch=int(datetime(2017,4,30,16,0,tzinfo=timezone.utc).timestamp())
    side=json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':str(epoch)},'geoData':{'latitude':34.6851,'longitude':135.8048}})
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':side})
    a=LibraryPlanner([z],embedded_provider=lambda m,l: None,timezone_resolver=TZ()).plan()[0]
    assert a.date_fact.precision==DatePrecision.RANGE and a.output_dir.startswith('_Needs Placement/Date/')
    assert a.timezone_suggestion.startswith('2017-05-01T01:00:00+09:00') and any('suggestion' in w for w in a.warnings)


def test_timezone_enrichment_provenance_survives_materialization(tmp_path):
    from gpa.timezones import TimezoneEvidence
    class TZ:
        def evidence_at(self,lat,lon,capture_year):return TimezoneEvidence('Asia/Tokyo',Confidence.PROVEN,'TBB:2026c:since1970','historical boundary','2026a')
    epoch=int(datetime(2017,4,30,16,tzinfo=timezone.utc).timestamp());z=tmp_path/'z.zip';root=tmp_path/'archive'
    side=json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':str(epoch)},'geoData':{'latitude':34.6851,'longitude':135.8048}});mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':side})
    m=RunExecutor([z],root,timezone_resolver=TZ(),embedded_provider=lambda m,l: None).execute(run_id='r');aid=m['successes'][0]['asset_id']
    rec=json.loads(RevisionStore(root).revisions(aid)[0].read_text())
    assert rec['timezone']['zone']=='Asia/Tokyo' and rec['timezone']['dataset']=='TBB:2026c:since1970' and rec['timezone']['tzdata_version']=='2026a'
    assert rec['date']['value'].startswith('2017-05-01T01:00:00+09:00')


def test_review_context_can_suggest_april_to_may_without_mutating_unknown_asset(tmp_path):
    from gpa.review_context import suggest_sequence_date_range
    from gpa.planner import LogicalAsset
    from gpa.model import SourceRef
    def asset(aid,name,fact,model='iPhone 8'):
        a=LogicalAsset(aid,aid,occurrences=[SourceRef('z',f'D/{name}')],date_fact=fact,placement_fact=fact)
        a.embedded=EmbeddedMetadata(make='Apple',model=model);return a
    before=DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=datetime(2017,4,28,10),year=2017,month=4,source='camera')
    unknown=DateFact(DatePrecision.UNKNOWN,Confidence.UNKNOWN,source='none')
    after=DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=datetime(2017,5,3,11),year=2017,month=5,source='camera')
    rows=[asset('a','IMG_1001.JPG',before),asset('b','IMG_1002.JPG',unknown),asset('c','IMG_1003.JPG',after)]
    s=suggest_sequence_date_range(rows,'b')
    assert s and s.confidence==Confidence.PROBABLE and s.start==before.value and s.end==after.value
    assert 'same camera' in ' '.join(s.reasons) and rows[1].date_fact is unknown


def test_review_context_does_not_use_different_filename_family_or_camera():
    from gpa.review_context import suggest_sequence_date_range
    from gpa.planner import LogicalAsset
    from gpa.model import SourceRef
    def a(aid,name,dt,model):
        f=DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=dt,year=dt.year,month=dt.month,source='camera') if dt else DateFact(DatePrecision.UNKNOWN,Confidence.UNKNOWN)
        x=LogicalAsset(aid,aid,occurrences=[SourceRef('z',f'D/{name}')],date_fact=f);x.embedded=EmbeddedMetadata(make='Apple',model=model);return x
    rows=[a('x','IMG_9.JPG',datetime(2017,4,1),'iPhone 7'),a('t','IMG_10.JPG',None,'iPhone 8'),a('y','DSC_11.JPG',datetime(2017,5,1),'iPhone 8')]
    assert suggest_sequence_date_range(rows,'t') is None


def test_review_context_rejects_inverted_or_excessively_wide_neighbor_dates():
    from gpa.review_context import suggest_sequence_date_range
    from gpa.planner import LogicalAsset
    from gpa.model import SourceRef
    def x(aid,n,dt):
        f=DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=dt,year=dt.year,month=dt.month) if dt else DateFact(DatePrecision.UNKNOWN,Confidence.UNKNOWN)
        return LogicalAsset(aid,aid,occurrences=[SourceRef('z',f'D/IMG_{n}.JPG')],date_fact=f)
    assert suggest_sequence_date_range([x('a',1,datetime(2018,1,2)),x('b',2,None),x('c',3,datetime(2018,1,1))],'b') is None
    assert suggest_sequence_date_range([x('d',10,datetime(2017,1,1)),x('e',11,None),x('f',12,datetime(2018,1,1))],'e') is None


def test_user_month_decision_moves_unknown_asset_without_inventing_day_or_time(tmp_path):
    from gpa.review import DecisionJournal,date_decision,user_month
    z=tmp_path/'z.zip';root=tmp_path/'archive';journal=DecisionJournal(tmp_path/'decisions.jsonl')
    mkzip(z,{'D/a.jpg':b'A'});aid=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()[0].asset_id
    journal.append(date_decision(aid,user_month(2017,5),rationale='Recognized family trip'))
    m=RunExecutor([z],root,embedded_provider=lambda m,l: None,decision_journal=journal).execute(run_id='r')
    assert m['successes'][0]['media'].startswith('Photos/2017/05 - May/')
    rec=json.loads(RevisionStore(root).revisions(aid)[0].read_text());assert rec['date']['precision']=='month' and rec['date']['value'] is None
    xmp=(root/m['successes'][0]['xmp']).read_text();assert '2017-05' in xmp and '2017-05-01' not in xmp
    assert rec['user_decisions'][0]['rationale']=='Recognized family trip'


def test_user_april_may_range_remains_needs_placement(tmp_path):
    from gpa.review import DecisionJournal,date_decision,user_month_range
    z=tmp_path/'z.zip';root=tmp_path/'archive';journal=DecisionJournal(tmp_path/'j.jsonl');mkzip(z,{'D/a.jpg':b'A'})
    aid=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()[0].asset_id
    journal.append(date_decision(aid,user_month_range(2017,4,2017,5)))
    report=RunExecutor([z],root,embedded_provider=lambda m,l: None,decision_journal=journal).plan();a=report.assets[0]
    assert a.date_fact.precision==DatePrecision.RANGE and a.date_fact.confidence==Confidence.USER_CONFIRMED
    assert a.output_dir.startswith('_Needs Placement/Date/2017-04-01_to_2017-05-31')


def test_latest_user_date_decision_controls_projection_but_journal_history_survives(tmp_path):
    from gpa.review import DecisionJournal,date_decision,user_month
    z=tmp_path/'z.zip';root=tmp_path/'archive';j=DecisionJournal(tmp_path/'j.jsonl');mkzip(z,{'D/a.jpg':b'A'})
    aid=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()[0].asset_id
    j.append(date_decision(aid,user_month(2017,4),rationale='first memory'));j.append(date_decision(aid,user_month(2017,5),rationale='checked diary'))
    m=RunExecutor([z],root,embedded_provider=lambda m,l: None,decision_journal=j).execute(run_id='r')
    rec=json.loads(RevisionStore(root).revisions(aid)[0].read_text())
    assert rec['date']['month']==5 and len(rec['user_decisions'])==2 and rec['user_decisions'][0]['value']['month']==4


def test_user_text_location_is_searchable_but_never_creates_gps(tmp_path):
    from gpa.review import DecisionJournal,location_label_decision
    z=tmp_path/'z.zip';root=tmp_path/'archive';j=DecisionJournal(tmp_path/'j.jsonl');mkzip(z,{'D/a.jpg':b'A'})
    aid=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()[0].asset_id;j.append(location_label_decision(aid,'Nara, Japan'))
    m=RunExecutor([z],root,embedded_provider=lambda m,l: None,decision_journal=j).execute(run_id='r')
    rec=json.loads(RevisionStore(root).revisions(aid)[0].read_text());assert rec['user_location_label']=='Nara, Japan' and rec['location']['lat'] is None
    c=rebuild_catalog_from_archive(root,root/'catalog.sqlite');assert c.by_place('Nara, Japan')==[(aid,m['successes'][0]['media'])];c.close()


def test_user_confirmed_gps_overrides_effective_location_but_preserves_google_candidate(tmp_path):
    from gpa.review import DecisionJournal,location_gps_decision
    z=tmp_path/'z.zip';root=tmp_path/'archive';j=DecisionJournal(tmp_path/'j.jsonl')
    side=json.dumps({'title':'a.jpg','geoData':{'latitude':35.0,'longitude':136.0}});mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':side})
    aid=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()[0].asset_id;j.append(location_gps_decision(aid,34.6851,135.8048,rationale='selected exact map point'))
    m=RunExecutor([z],root,embedded_provider=lambda m,l: None,decision_journal=j).execute(run_id='r');rec=json.loads(RevisionStore(root).revisions(aid)[0].read_text())
    assert rec['location']['confidence']=='user_confirmed' and rec['location']['lat']==34.6851 and rec['location']['current_candidates']==[[35.0,136.0]]
    xmp=(root/m['successes'][0]['xmp']).read_text();assert 'GPSLatitude' in xmp and '34,41.106' in xmp


def test_user_month_decision_propagates_live_photo_folder_placement_only(tmp_path):
    from gpa.review import DecisionJournal,date_decision,user_month
    from gpa.executor import RunExecutor
    from gpa.model import DatePrecision,Confidence
    z=tmp_path/'z.zip';mkzip(z,{'D/IMG_1.HEIC':b'STILL','D/IMG_1.MOV':b'MOVIE'})
    def embedded(m,lib):
        return EmbeddedMetadata(content_identifier='CID-1')
    initial=LibraryPlanner([z],embedded_provider=embedded).plan()
    still=next(a for a in initial if a.occurrences[0].path.endswith('.HEIC'))
    journal=DecisionJournal(tmp_path/'j.jsonl');journal.append(date_decision(still.asset_id,user_month(2017,5)))
    report=RunExecutor([z],tmp_path/'archive',embedded_provider=embedded,decision_journal=journal).plan()
    by_ext={Path(a.occurrences[0].path).suffix.upper():a for a in report.assets}
    s=by_ext['.HEIC'];m=by_ext['.MOV']
    assert s.output_dir=='Photos/2017/05 - May' and m.output_dir=='Photos/2017/05 - May'
    assert m.date_fact.precision==DatePrecision.UNKNOWN
    assert m.placement_fact.precision==DatePrecision.MONTH
    assert m.placement_fact.confidence==Confidence.USER_CONFIRMED
    assert m.placement_fact.source=='live-photo-companion-placement'


def test_live_photo_user_correction_conflicting_with_companion_month_returns_both_to_review(tmp_path):
    from gpa.review import DecisionJournal,date_decision,user_month
    from gpa.executor import RunExecutor
    from gpa.model import Confidence
    may='1494806400'  # 2017-05-15 UTC, safe month
    june='1497484800' # 2017-06-15 UTC, safe month
    z=tmp_path/'z.zip';mkzip(z,{
        'D/IMG_1.HEIC':b'STILL','D/IMG_1.HEIC.json':gjson('IMG_1.HEIC',may),
        'D/IMG_1.MOV':b'MOVIE','D/IMG_1.MOV.json':gjson('IMG_1.MOV',june),
    })
    def embedded(m,lib):return EmbeddedMetadata(content_identifier='CID-2')
    initial=LibraryPlanner([z],embedded_provider=embedded).plan()
    still=next(a for a in initial if a.occurrences[0].path.endswith('.HEIC'))
    journal=DecisionJournal(tmp_path/'j.jsonl');journal.append(date_decision(still.asset_id,user_month(2017,5)))
    report=RunExecutor([z],tmp_path/'archive',embedded_provider=embedded,decision_journal=journal).plan()
    assert all(a.placement_fact.confidence==Confidence.CONFLICT for a in report.assets)
    assert all(a.output_dir=='_Needs Placement/Date/Unknown' for a in report.assets)
    assert all(any('Live Photo components disagree on month' in w for w in a.warnings) for a in report.assets)


def test_reapplying_live_photo_placement_does_not_duplicate_family_relation(tmp_path):
    from gpa.review import DecisionJournal,date_decision,user_month
    from gpa.executor import RunExecutor
    z=tmp_path/'z.zip';mkzip(z,{'D/A.HEIC':b'A','D/A.MOV':b'B'})
    def embedded(m,lib):return EmbeddedMetadata(content_identifier='CID-3')
    initial=LibraryPlanner([z],embedded_provider=embedded).plan();still=next(a for a in initial if a.occurrences[0].path.endswith('.HEIC'))
    j=DecisionJournal(tmp_path/'j.jsonl');j.append(date_decision(still.asset_id,user_month(2020,1)))
    report=RunExecutor([z],tmp_path/'archive',embedded_provider=embedded,decision_journal=j).plan()
    for a in report.assets:
        live=[r for r in a.family_relations if r.get('kind')=='apple-live-photo']
        assert len(live)==1


def test_incremental_storage_preflight_uses_permanent_plus_largest_transient(tmp_path,monkeypatch):
    from gpa.storage import StorageOperation,preflight_operations
    import gpa.storage as st
    class DU: free=10_000_000
    monkeypatch.setattr(st.shutil,'disk_usage',lambda p:DU())
    ops=[
        StorageOperation('a','new',permanent_growth=1000,transient_growth=700,written_file_sizes=(700,)),
        StorageOperation('b','relocated',permanent_growth=100,transient_growth=5000,written_file_sizes=(5000,)),
        StorageOperation('c','metadata_update',permanent_growth=200,transient_growth=300,written_file_sizes=(300,)),
    ]
    r=preflight_operations(tmp_path/'not/yet/created',ops,reserve_fraction=0,global_metadata_reserve=50)
    assert r.ok and r.permanent_growth_bytes==1300 and r.transient_peak_bytes==5000
    assert r.required_bytes==6350 and r.operations=={'new':1,'relocated':1,'metadata_update':1}


def test_incremental_storage_preflight_fat32_checks_only_files_that_must_be_written(tmp_path,monkeypatch):
    from gpa.storage import StorageOperation,preflight_operations
    import gpa.storage as st
    class DU: free=20*1024**3
    monkeypatch.setattr(st.shutil,'disk_usage',lambda p:DU())
    huge=4*1024**3+1
    unchanged=preflight_operations(tmp_path,[StorageOperation('a','unchanged',0,0,())],fs_type='fat32',reserve_fraction=0,global_metadata_reserve=0)
    blocked=preflight_operations(tmp_path,[StorageOperation('b','relocated',0,huge,(huge,))],fs_type='fat32',reserve_fraction=0,global_metadata_reserve=0)
    assert unchanged.ok and not blocked.ok and 'FAT32' in blocked.errors[0]


def test_executor_storage_preflight_classifies_first_import_as_new(tmp_path):
    from gpa.executor import RunExecutor
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A'*4096,'D/a.jpg.json':gjson('a.jpg')})
    ex=RunExecutor([z],tmp_path/'archive',embedded_provider=lambda m,l: None,storage_reserve_fraction=0)
    report=ex.plan();pf=ex.storage_preflight(report)
    assert pf.ok and pf.operations.get('new')==1
    assert pf.permanent_growth_bytes>=4096 and pf.transient_peak_bytes>=4096


def test_executor_unchanged_reimport_does_not_reserve_existing_media_again(tmp_path):
    from gpa.executor import RunExecutor
    z=tmp_path/'z.zip';root=tmp_path/'archive';import random;payload=random.Random(1).randbytes(2*1024*1024)
    mkzip(z,{'D/a.jpg':payload,'D/a.jpg.json':gjson('a.jpg')})
    RunExecutor([z],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0).execute(run_id='first')
    ex=RunExecutor([z],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0);pf=ex.storage_preflight(ex.plan())
    assert pf.operations.get('unchanged')==1
    assert pf.permanent_growth_bytes==0 and pf.transient_peak_bytes==0
    assert pf.required_bytes==1024*1024  # fixed run/metadata safety reserve only


def test_executor_metadata_update_reserves_history_not_full_media(tmp_path):
    from gpa.executor import RunExecutor
    root=tmp_path/'archive';z1=tmp_path/'a.zip';z2=tmp_path/'b.zip';import random;payload=random.Random(2).randbytes(2*1024*1024)
    mkzip(z1,{'D/a.jpg':payload,'D/a.jpg.json':json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':'1494806400'},'description':'old'})})
    RunExecutor([z1],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0).execute(run_id='first')
    mkzip(z2,{'D/a.jpg':payload,'D/a.jpg.json':json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':'1494806400'},'description':'new'})})
    ex=RunExecutor([z2],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0);pf=ex.storage_preflight(ex.plan())
    assert pf.operations.get('metadata_update')==1
    assert pf.permanent_growth_bytes < len(payload)//10
    assert pf.transient_peak_bytes < len(payload)//10


def test_executor_relocation_reserves_transient_media_copy_but_not_second_permanent_media(tmp_path):
    from gpa.executor import RunExecutor
    root=tmp_path/'archive';z1=tmp_path/'a.zip';z2=tmp_path/'b.zip';import random;payload=random.Random(3).randbytes(2*1024*1024)
    # Safe mid-month epochs avoid timezone month ambiguity.
    mkzip(z1,{'D/a.jpg':payload,'D/a.jpg.json':gjson('a.jpg','1579089600')})  # Jan 2020
    RunExecutor([z1],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0).execute(run_id='first')
    mkzip(z2,{'D/a.jpg':payload,'D/a.jpg.json':gjson('a.jpg','1576411200')})  # Dec 2019
    ex=RunExecutor([z2],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0);report=ex.plan();pf=ex.storage_preflight(report)
    assert pf.operations.get('relocated')==1
    assert pf.transient_peak_bytes>=len(payload)
    assert pf.permanent_growth_bytes < len(payload)//10


def test_review_queue_unknown_date_is_blocking_but_never_assigns_a_date(tmp_path):
    from gpa.review_queue import build_review_queue
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A'})
    a=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()[0]
    q=build_review_queue([a]);assert len(q)==1 and q[0].blocks_placement
    assert 'confirm_month' in q[0].decision_options and a.date_fact.precision==DatePrecision.UNKNOWN


def test_review_queue_known_month_with_unknown_location_is_optional_not_blocking(tmp_path):
    from gpa.review_queue import build_review_queue
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':gjson('a.jpg')})
    a=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan()[0]
    q=build_review_queue([a]);assert len(q)==1 and not q[0].blocks_placement
    assert q[0].optional==['exact location/GPS is unknown'] and 'confirm_exact_gps' in q[0].decision_options


def test_review_queue_includes_non_authoritative_sequence_suggestion():
    from gpa.review_queue import build_review_queue
    from gpa.planner import LogicalAsset
    from gpa.model import SourceRef
    from datetime import datetime
    def x(aid,n,dt):
        f=DateFact(DatePrecision.INSTANT,Confidence.PROVEN,value=dt,year=dt.year,month=dt.month,source='camera') if dt else DateFact(DatePrecision.UNKNOWN,Confidence.UNKNOWN,source='none')
        return LogicalAsset(aid,aid,occurrences=[SourceRef('z',f'D/IMG_{n}.JPG')],date_fact=f,placement_fact=f,output_dir='_Needs Placement/Date/Unknown',output_name=f'IMG_{n}.JPG')
    rows=[x('a',1001,datetime(2017,4,28,10)),x('b',1002,None),x('c',1003,datetime(2017,5,3,11))]
    item=next(i for i in build_review_queue(rows) if i.asset_id=='b')
    assert item.date_suggestion and item.date_suggestion['confidence']=='probable'
    assert item.date_suggestion['start'].startswith('2017-04-28') and item.date_suggestion['end'].startswith('2017-05-03')
    assert rows[1].date_fact.precision==DatePrecision.UNKNOWN


def test_review_queue_json_is_open_and_roundtrippable(tmp_path):
    from gpa.review_queue import build_review_queue,write_review_queue
    z=tmp_path/'z.zip';mkzip(z,{'D/a.jpg':b'A'})
    items=build_review_queue(LibraryPlanner([z],embedded_provider=lambda m,l: None).plan())
    p=write_review_queue(tmp_path/'review.json',items);obj=json.loads(p.read_text())
    assert obj['schema']=='gpa.review-queue.v1' and obj['items'][0]['asset_id']==items[0].asset_id
    assert obj['items'][0]['blocks_placement'] is True


def test_planning_and_storage_preflight_are_strictly_read_only(tmp_path):
    from gpa.executor import RunExecutor
    z=tmp_path/'z.zip';root=tmp_path/'destination-does-not-exist';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':gjson('a.jpg')})
    ex=RunExecutor([z],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0)
    report=ex.plan();pf=ex.storage_preflight(report)
    assert pf.ok and not root.exists()


def test_run_preview_is_read_only_and_reports_new_action(tmp_path):
    from gpa.executor import RunExecutor
    from gpa.preview import build_run_preview
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':gjson('a.jpg')})
    ex=RunExecutor([z],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0)
    p=build_run_preview(ex)
    assert p.status=='ready' and p.actions=={'new':1} and p.assets_planned==1
    assert p.blocking_review_items==0 and p.optional_review_items==1
    assert not root.exists()


def test_run_preview_unknown_date_is_needs_review_not_execution_blocked(tmp_path):
    from gpa.executor import RunExecutor
    from gpa.preview import build_run_preview
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'A'})
    p=build_run_preview(RunExecutor([z],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0))
    assert p.status=='needs_review' and p.can_execute and p.blocking_review_items==1
    assert p.actions=={'new':1}


def test_run_preview_storage_failure_blocks_execution_without_writing(tmp_path,monkeypatch):
    from gpa.executor import RunExecutor
    from gpa.preview import build_run_preview
    import gpa.storage as st
    class DU: free=512
    monkeypatch.setattr(st.shutil,'disk_usage',lambda p:DU())
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'A'*4096,'D/a.jpg.json':gjson('a.jpg')})
    p=build_run_preview(RunExecutor([z],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0))
    assert p.status=='blocked' and not p.can_execute and p.storage_errors
    assert not root.exists()


def test_source_vault_preserves_raw_takeout_zip_byte_for_byte(tmp_path):
    from gpa.source_vault import vault_source_archive,audit_source_vault
    z=tmp_path/'takeout-001.zip';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':gjson('a.jpg')});before=z.read_bytes()
    row=vault_source_archive(z,tmp_path/'vault')
    assert z.read_bytes()==before
    vaulted=(tmp_path/'vault'/row.vaulted_path);assert vaulted.read_bytes()==before
    assert audit_source_vault(tmp_path/'vault').ok


def test_source_vault_deduplicates_identical_archive_bytes_but_keeps_occurrence_records(tmp_path):
    from gpa.source_vault import vault_source_archives,audit_source_vault
    a=tmp_path/'one.zip';b=tmp_path/'two.zip';mkzip(a,{'x':b'123'});b.write_bytes(a.read_bytes())
    rows=vault_source_archives([a,b],tmp_path/'vault')
    assert rows[0].vaulted_path==rows[1].vaulted_path and rows[1].reused_existing_bytes
    assert rows[0].record_path!=rows[1].record_path
    audit=audit_source_vault(tmp_path/'vault');assert audit.ok and audit.records_checked==2 and audit.archives_checked==1


def test_source_vault_refuses_corrupted_existing_content_address(tmp_path):
    from gpa.source_vault import vault_source_archive
    z=tmp_path/'t.zip';mkzip(z,{'x':b'123'});row=vault_source_archive(z,tmp_path/'vault')
    (tmp_path/'vault'/row.vaulted_path).write_bytes(b'CORRUPT')
    with pytest.raises(RuntimeError):vault_source_archive(z,tmp_path/'vault')


def test_source_vault_audit_detects_tampering(tmp_path):
    from gpa.source_vault import vault_source_archive,audit_source_vault
    z=tmp_path/'t.zip';mkzip(z,{'x':b'123'});row=vault_source_archive(z,tmp_path/'vault')
    (tmp_path/'vault'/row.vaulted_path).write_bytes(b'changed')
    a=audit_source_vault(tmp_path/'vault');assert not a.ok and any('mismatch' in x for x in a.problems)


def test_source_vault_reimport_same_source_is_idempotent(tmp_path):
    from gpa.source_vault import vault_source_archive
    z=tmp_path/'t.zip';mkzip(z,{'x':b'123'});v=tmp_path/'vault'
    first=vault_source_archive(z,v);second=vault_source_archive(z,v)
    assert first.vaulted_path==second.vaulted_path and second.reused_existing_bytes
    assert first.record_path==second.record_path


def test_tampered_source_vault_blocks_google_retirement_gate(tmp_path):
    from gpa.source_vault import vault_source_archive
    from gpa.verification import verify_migration
    from gpa.transactions import sha256_file
    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw');digest=sha256_file(raw)
    root=tmp_path/'archive';(root/'metadata'/'assets').mkdir(parents=True);(root/'metadata'/'runs').mkdir(parents=True)
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':[{'path':str(raw),'sha256':digest}], 'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    vault=tmp_path/'vault';row=vault_source_archive(raw,vault)
    (vault/row.vaulted_path).write_bytes(b'tampered')
    v=verify_migration(root,all_takeout_parts_confirmed=True,redundant_copy_verified=True,source_vault_root=vault)
    assert not v.source_vault_verified and not v.google_retirement_ready
    assert any('source vault' in x.lower() for x in v.blockers)


def test_unrelated_healthy_source_vault_does_not_satisfy_run_source_fingerprints(tmp_path):
    from gpa.source_vault import vault_source_archive
    from gpa.verification import verify_migration
    from gpa.transactions import sha256_file
    actual=tmp_path/'actual.zip';actual.write_bytes(b'ACTUAL');digest=sha256_file(actual)
    other=tmp_path/'other.zip';other.write_bytes(b'OTHER');vault=tmp_path/'vault';vault_source_archive(other,vault)
    root=tmp_path/'archive';(root/'metadata'/'assets').mkdir(parents=True);(root/'metadata'/'runs').mkdir(parents=True)
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':[{'path':str(actual),'sha256':digest}], 'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    v=verify_migration(root,all_takeout_parts_confirmed=True,redundant_copy_verified=True,source_vault_root=vault)
    assert not v.source_vault_verified and not v.google_retirement_ready
    assert any('missing 1 Takeout archive fingerprint' in x for x in v.notes)



def test_filesystem_type_detection_is_read_only_and_best_effort(tmp_path):
    from gpa.storage import detect_filesystem_type
    target=tmp_path/'does/not/exist';got=detect_filesystem_type(target)
    assert got is None or isinstance(got,str)
    assert not target.exists()


def test_automatic_filesystem_detection_can_enforce_fat32_limit(tmp_path,monkeypatch):
    import gpa.storage as st
    class DU: free=20*1024**3
    monkeypatch.setattr(st,'detect_filesystem_type',lambda p:'fat32');monkeypatch.setattr(st.shutil,'disk_usage',lambda p:DU())
    huge=4*1024**3+1
    r=st.preflight_operations(tmp_path,[st.StorageOperation('a','new',huge,huge,(huge,))],reserve_fraction=0,global_metadata_reserve=0)
    assert not r.ok and r.filesystem_type=='fat32' and any('FAT32' in e for e in r.errors)


def test_run_manifest_records_destination_filesystem_type(tmp_path):
    from gpa.executor import RunExecutor
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':gjson('a.jpg')})
    m=RunExecutor([z],root,embedded_provider=lambda m,l: None,destination_fs_type='ntfs',storage_reserve_fraction=0).execute(run_id='r')
    assert m['storage_preflight']['filesystem_type']=='ntfs'


def test_cli_preview_is_json_and_read_only(tmp_path,capsys):
    from gpa.__main__ import main
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':gjson('a.jpg')})
    rc=main(['preview','--input',str(z),'--output',str(root),'--include-review','--reserve-fraction','0'])
    obj=json.loads(capsys.readouterr().out);assert rc==0 and obj['schema']=='gpa.preview.v1' and obj['actions']=={'new':1}
    assert not root.exists()


def test_cli_run_then_audit_roundtrip(tmp_path,capsys):
    from gpa.__main__ import main
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':gjson('a.jpg')})
    assert main(['run','--input',str(z),'--output',str(root),'--run-id','r','--reserve-fraction','0'])==0
    run=json.loads(capsys.readouterr().out);assert run['status']=='complete'
    assert main(['audit','--output',str(root)])==0
    audit=json.loads(capsys.readouterr().out);assert audit['ok'] and audit['assets_checked']==1


def test_cli_vault_preserves_and_audits_source(tmp_path,capsys):
    from gpa.__main__ import main
    z=tmp_path/'z.zip';mkzip(z,{'x':b'1'});vault=tmp_path/'vault'
    assert main(['vault','--input',str(z),'--vault',str(vault)])==0
    obj=json.loads(capsys.readouterr().out);assert obj['audit']['ok'] and obj['audit']['archives_checked']==1


def test_cli_verify_is_nonzero_until_retirement_gates_are_met(tmp_path,capsys):
    from gpa.__main__ import main
    root=tmp_path/'archive';(root/'metadata'/'assets').mkdir(parents=True);(root/'metadata'/'runs').mkdir(parents=True)
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':[], 'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    assert main(['verify','--output',str(root)])==1
    obj=json.loads(capsys.readouterr().out);assert not obj['google_retirement_ready'] and obj['blockers']


def _minimal_verified_root_for_retirement(root,source_hash):
    (root/'metadata'/'assets').mkdir(parents=True);(root/'metadata'/'runs').mkdir(parents=True)
    from gpa.archive_format import upgrade_archive_format
    upgrade_archive_format(root)
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':[{'path':'takeout.zip','sha256':source_hash}], 'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))


def test_retirement_verifier_can_hash_verify_actual_redundant_copy(tmp_path,monkeypatch):
    from gpa.source_vault import vault_source_archive
    from gpa.verification import verify_migration
    from gpa.transactions import sha256_file
    import shutil
    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw');digest=sha256_file(raw);root=tmp_path/'archive';_minimal_verified_root_for_retirement(root,digest)
    vault=tmp_path/'vault';vault_source_archive(raw,vault)
    mirror=tmp_path/'mirror';shutil.copytree(root,mirror)
    import gpa.storage_identity as sid
    monkeypatch.setattr(sid.platform,'system',lambda:'Linux')
    real=sid._device_token
    monkeypatch.setattr(sid,'_device_token',lambda p: 'archive-primary' if Path(p).resolve()==root.resolve() else ('archive-backup' if Path(p).resolve()==mirror.resolve() else real(p)))
    v=verify_migration(root,all_takeout_parts_confirmed=True,source_vault_root=vault,redundant_copy_root=mirror,redundant_copy_separate_media_confirmed=True,require_source_vault_redundancy=False,require_takeout_receipt=False,require_exit_evidence=False)
    assert v.redundant_copy_verified and v.redundant_copy_files_checked>=1 and v.google_retirement_ready


def test_retirement_verifier_detects_damaged_redundant_copy(tmp_path):
    from gpa.source_vault import vault_source_archive
    from gpa.verification import verify_migration
    from gpa.transactions import sha256_file
    import shutil
    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw');digest=sha256_file(raw);root=tmp_path/'archive';_minimal_verified_root_for_retirement(root,digest)
    vault=tmp_path/'vault';vault_source_archive(raw,vault);mirror=tmp_path/'mirror';shutil.copytree(root,mirror)
    (mirror/'metadata'/'runs'/'r.json').write_text('tampered')
    v=verify_migration(root,all_takeout_parts_confirmed=True,source_vault_root=vault,redundant_copy_root=mirror)
    assert not v.redundant_copy_verified and not v.google_retirement_ready
    assert any('Redundant-copy audit found' in n for n in v.notes)


def test_cli_verify_can_use_actual_redundant_copy_path(tmp_path,capsys,monkeypatch):
    from gpa.__main__ import main
    from gpa.source_vault import vault_source_archive
    from gpa.transactions import sha256_file
    import shutil
    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw');digest=sha256_file(raw);root=tmp_path/'archive';_minimal_verified_root_for_retirement(root,digest)
    vault=tmp_path/'vault';vault_source_archive(raw,vault)
    vaultmirror=tmp_path/'vaultmirror';shutil.copytree(vault,vaultmirror)
    mirror=tmp_path/'mirror';shutil.copytree(root,mirror)
    import gpa.storage_identity as sid
    monkeypatch.setattr(sid.platform,'system',lambda:'Linux')
    real=sid._device_token
    tokens={root.resolve():'archive-primary',mirror.resolve():'archive-backup',vault.resolve():'vault-primary',vaultmirror.resolve():'vault-backup'}
    monkeypatch.setattr(sid,'_device_token',lambda p: tokens.get(Path(p).resolve(),real(p)))
    receipt=tmp_path/'receipt.json';exitp=tmp_path/'exit.json'
    from gpa.takeout_receipt import create_takeout_receipt,write_takeout_receipt
    from gpa.exit_evidence import create_exit_evidence,write_exit_evidence
    write_takeout_receipt(receipt,create_takeout_receipt([raw],expected_part_count=1))
    write_exit_evidence(exitp,create_exit_evidence(shared_albums_reviewed=True,wanted_shared_media_secured=True,partner_sharing_reviewed=True,locked_folder_reviewed=True,takeout_scope_reviewed=True,recent_changes_accounted_for=True))
    rc=main(['verify','--output',str(root),'--source-vault',str(vault),'--source-vault-redundant-copy',str(vaultmirror),'--redundant-copy',str(mirror),'--takeout-receipt',str(receipt),'--exit-evidence',str(exitp),'--all-parts-confirmed','--confirm-redundant-copy-separate-media','--confirm-source-vault-copy-separate-media'])
    obj=json.loads(capsys.readouterr().out);assert rc==0 and obj['google_retirement_ready'] and obj['redundant_copy_verified']


def test_takeout_part_analysis_detects_internal_gap_but_not_unknown_trailing_part():
    from gpa.takeout_parts import analyze_takeout_parts
    a=analyze_takeout_parts(['takeout-20260821T010203Z-001.zip','takeout-20260821T010203Z-003.zip'])
    assert not a.ok and a.groups[0].missing_internal==(2,)
    b=analyze_takeout_parts(['takeout-20260821T010203Z-001.zip','takeout-20260821T010203Z-002.zip'])
    assert b.ok and b.known_gaps==0  # cannot infer an unseen trailing 003 from names alone


def test_takeout_part_analysis_can_use_user_confirmed_expected_count():
    from gpa.takeout_parts import analyze_takeout_parts
    key='takeout-20260821t010203z-.zip'
    a=analyze_takeout_parts(['takeout-20260821T010203Z-001.zip','takeout-20260821T010203Z-002.zip'],{key:4})
    assert not a.ok and a.groups[0].missing_expected==(3,4)


def test_retirement_gate_cannot_override_obvious_takeout_part_gap(tmp_path):
    from gpa.verification import verify_migration
    root=tmp_path/'archive';(root/'metadata'/'assets').mkdir(parents=True);(root/'metadata'/'runs').mkdir(parents=True)
    rows=[{'path':'takeout-20260821T010203Z-001.zip','sha256':'1'*64},{'path':'takeout-20260821T010203Z-003.zip','sha256':'3'*64}]
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':rows,'source_problems':[], 'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    v=verify_migration(root,all_takeout_parts_confirmed=True,redundant_copy_verified=True,require_source_vault=False)
    assert not v.google_retirement_ready and v.takeout_sequence_known_gaps==1
    assert any('known missing' in x for x in v.blockers)


def test_run_preview_surfaces_obvious_takeout_part_gap_before_writing(tmp_path):
    from gpa.executor import RunExecutor
    from gpa.preview import build_run_preview
    a=tmp_path/'takeout-20260821T010203Z-001.zip';c=tmp_path/'takeout-20260821T010203Z-003.zip'
    mkzip(a,{'D/a.jpg':b'A'});mkzip(c,{'D/c.jpg':b'C'})
    root=tmp_path/'archive';p=build_run_preview(RunExecutor([a,c],root,embedded_provider=lambda m,l: None,storage_reserve_fraction=0))
    assert p.status=='partial_source' and p.takeout_sequence_known_gaps==1 and not p.source_understanding_complete
    assert not root.exists()


def _gjson_full(title,ts='1494806400',url=None,description='',lat=None,lon=None):
    d={'title':title,'photoTakenTime':{'timestamp':ts},'description':description}
    if url:d['url']=url
    if lat is not None and lon is not None:d['geoData']={'latitude':lat,'longitude':lon}
    return json.dumps(d)


def test_exact_duplicate_album_and_year_copy_same_url_is_one_content_with_identity_hint(tmp_path):
    z=tmp_path/'takeout.zip';u='https://photos.google.com/photo/AF1QipSame'
    album_meta=json.dumps({'title':'Trip','date':{'timestamp':'1'}})
    mkzip(z,{
        'Google Photos/Photos from 2017/a.jpg':b'SAME',
        'Google Photos/Photos from 2017/a.jpg.json':_gjson_full('a.jpg',url=u),
        'Google Photos/Trip/a.jpg':b'SAME',
        'Google Photos/Trip/a.jpg.json':_gjson_full('a.jpg',url=u),
        'Google Photos/Trip/metadata.json':album_meta,
    })
    a=LibraryPlanner([z],embedded_provider=lambda m,l:None).plan()[0]
    assert len(a.occurrences)==2 and a.content_identity is not None
    assert a.content_identity.possible_logical_item_count==1
    assert a.content_identity.distinct_url_hints==[u]
    assert not a.content_identity.needs_identity_review
    assert {x['path'] for x in a.occurrence_contexts}=={'Google Photos/Photos from 2017/a.jpg','Google Photos/Trip/a.jpg'}
    trip=next(x for x in a.occurrence_contexts if x['path'].startswith('Google Photos/Trip/'))
    assert trip['album_title']=='Trip'


def test_identical_media_with_different_google_url_hints_preserves_possible_item_multiplicity(tmp_path):
    z=tmp_path/'takeout.zip'
    mkzip(z,{
        'Google Photos/Photos from 2017/a.jpg':b'SAME',
        'Google Photos/Photos from 2017/a.jpg.json':_gjson_full('a.jpg',url='https://photos.google.com/photo/ONE'),
        'Google Photos/Other/a.jpg':b'SAME',
        'Google Photos/Other/a.jpg.json':_gjson_full('a.jpg',url='https://photos.google.com/photo/TWO'),
    })
    rows=LibraryPlanner([z],embedded_provider=lambda m,l:None).plan()
    assert len(rows)==1  # one content object; logical-item multiplicity is retained inside it
    ev=rows[0].content_identity
    assert ev and ev.needs_identity_review and ev.possible_logical_item_count==2
    assert ev.multiplicity_confidence=='probable'
    assert len(ev.distinct_url_hints)==2
    assert any('logical Google Photos items' in w for w in rows[0].warnings)


def test_identical_media_with_materially_different_sidecars_preserves_clusters_without_url(tmp_path):
    z=tmp_path/'takeout.zip'
    mkzip(z,{
        'A/a.jpg':b'SAME','A/a.jpg.json':_gjson_full('a.jpg',ts='1494806400',description='one'),
        'B/a.jpg':b'SAME','B/a.jpg.json':_gjson_full('a.jpg',ts='1497484800',description='two'),
    })
    a=LibraryPlanner([z],embedded_provider=lambda m,l:None).plan()[0]
    ev=a.content_identity
    assert ev and ev.needs_identity_review and len(ev.clusters)==2
    assert ev.possible_logical_item_count==2
    # Conflicting months must still block exact placement rather than allowing content dedupe to erase the conflict.
    assert a.date_fact.confidence==Confidence.CONFLICT


def test_asset_record_persists_content_identity_and_per_occurrence_context(tmp_path):
    z=tmp_path/'takeout.zip';u='https://photos.google.com/photo/ONE'
    mkzip(z,{'D/a.jpg':b'A','D/a.jpg.json':_gjson_full('a.jpg',url=u)})
    ex=__import__('gpa.executor',fromlist=['RunExecutor']).RunExecutor([z],tmp_path/'archive',embedded_provider=lambda m,l:None,storage_reserve_fraction=0)
    m=ex.execute(run_id='r');aid=m['successes'][0]['asset_id']
    rev=RevisionStore(tmp_path/'archive').revisions(aid)[0]
    obj=json.loads(rev.read_text())
    assert obj['content_identity']['content_sha256']==hashlib.sha256(b'A').hexdigest()
    assert obj['content_identity']['distinct_url_hints']==[u]
    assert obj['occurrence_contexts'][0]['path']=='D/a.jpg'


def _complete_exit_file(path:Path):
    from gpa.exit_evidence import create_exit_evidence,write_exit_evidence
    e=create_exit_evidence(shared_albums_reviewed=True,wanted_shared_media_secured=True,partner_sharing_reviewed=True,locked_folder_reviewed=True,takeout_scope_reviewed=True,recent_changes_accounted_for=True)
    write_exit_evidence(path,e);return path


def test_takeout_receipt_binds_expected_count_and_exact_run_hashes(tmp_path):
    from gpa.takeout_receipt import create_takeout_receipt,write_takeout_receipt,verify_takeout_receipts
    from gpa.transactions import sha256_file
    a=tmp_path/'takeout-20260821T010203Z-001.zip';b=tmp_path/'takeout-20260821T010203Z-002.zip'
    a.write_bytes(b'A');b.write_bytes(b'B')
    rp=tmp_path/'receipt.json';write_takeout_receipt(rp,create_takeout_receipt([a,b],expected_part_count=2,confirmation_source='takeout_ui'))
    rows=[{'path':str(a),'sha256':sha256_file(a)},{'path':str(b),'sha256':sha256_file(b)}]
    v=verify_takeout_receipts([rp],rows)
    assert v.ok and v.expected_parts==2 and v.matched_parts==2 and len(v.covered_run_hashes)==2


def test_takeout_receipt_refuses_missing_confirmed_trailing_part(tmp_path):
    from gpa.takeout_receipt import create_takeout_receipt,write_takeout_receipt,verify_takeout_receipts
    from gpa.transactions import sha256_file
    a=tmp_path/'takeout-20260821T010203Z-001.zip';b=tmp_path/'takeout-20260821T010203Z-002.zip';a.write_bytes(b'A');b.write_bytes(b'B')
    rp=tmp_path/'receipt.json';r=create_takeout_receipt([a,b],expected_part_count=3);write_takeout_receipt(rp,r)
    rows=[{'path':str(a),'sha256':sha256_file(a)},{'path':str(b),'sha256':sha256_file(b)}]
    v=verify_takeout_receipts([rp],rows)
    assert not v.ok and any('expected 3' in x or 'missing' in x for x in v.problems)


def test_takeout_receipt_requires_every_migration_source_hash_to_be_covered(tmp_path):
    from gpa.takeout_receipt import create_takeout_receipt,write_takeout_receipt,verify_takeout_receipts
    from gpa.transactions import sha256_file
    a=tmp_path/'takeout-001.zip';b=tmp_path/'takeout-002.zip';a.write_bytes(b'A');b.write_bytes(b'B')
    rp=tmp_path/'receipt.json';write_takeout_receipt(rp,create_takeout_receipt([a],expected_part_count=1))
    rows=[{'path':str(a),'sha256':sha256_file(a)},{'path':str(b),'sha256':sha256_file(b)}]
    v=verify_takeout_receipts([rp],rows)
    assert not v.ok and any('not covered' in x for x in v.problems)


def test_exit_evidence_requires_shared_partner_locked_scope_and_late_change_review(tmp_path):
    from gpa.exit_evidence import create_exit_evidence,write_exit_evidence,verify_exit_evidence
    p=tmp_path/'exit.json';write_exit_evidence(p,create_exit_evidence(shared_albums_reviewed=True))
    ok,problems=verify_exit_evidence(p);assert not ok and len(problems)>=5
    _complete_exit_file(p);ok,problems=verify_exit_evidence(p);assert ok and not problems


def test_strict_google_retirement_gate_requires_receipt_and_exit_evidence(tmp_path,monkeypatch):
    from gpa.source_vault import vault_source_archives
    from gpa.transactions import sha256_file
    from gpa.takeout_receipt import create_takeout_receipt,write_takeout_receipt
    from gpa.verification import verify_migration
    import shutil
    a=tmp_path/'takeout-20260821T010203Z-001.zip';b=tmp_path/'takeout-20260821T010203Z-002.zip';a.write_bytes(b'A');b.write_bytes(b'B')
    root=tmp_path/'archive';(root/'metadata/assets').mkdir(parents=True);(root/'metadata/runs').mkdir(parents=True)
    from gpa.archive_format import upgrade_archive_format
    upgrade_archive_format(root)
    sources=[{'path':str(x),'sha256':sha256_file(x)} for x in (a,b)]
    (root/'metadata/runs/r.json').write_text(json.dumps({'status':'complete','source_archives':sources,'source_problems':[],'assets_planned':0,'assets_committed':0,'failures':[],'unclassified_preserved':[]}))
    vault=tmp_path/'vault';vault_source_archives([a,b],vault);vaultmirror=tmp_path/'vaultmirror';shutil.copytree(vault,vaultmirror);mirror=tmp_path/'mirror';shutil.copytree(root,mirror)
    assert not verify_migration(root,all_takeout_parts_confirmed=True,source_vault_root=vault,redundant_copy_root=mirror).google_retirement_ready
    import gpa.storage_identity as sid
    monkeypatch.setattr(sid.platform,'system',lambda:'Linux')
    real=sid._device_token
    tokens={root.resolve():'archive-primary',mirror.resolve():'archive-backup',vault.resolve():'vault-primary',vaultmirror.resolve():'vault-backup'}
    monkeypatch.setattr(sid,'_device_token',lambda p: tokens.get(Path(p).resolve(),real(p)))
    receipt=tmp_path/'receipt.json';write_takeout_receipt(receipt,create_takeout_receipt([a,b],expected_part_count=2))
    exitp=_complete_exit_file(tmp_path/'exit.json')
    v=verify_migration(root,all_takeout_parts_confirmed=True,source_vault_root=vault,source_vault_redundant_copy_root=vaultmirror,redundant_copy_root=mirror,redundant_copy_separate_media_confirmed=True,source_vault_redundant_separate_media_confirmed=True,takeout_receipts=[receipt],exit_evidence_path=exitp)
    assert v.google_retirement_ready and v.takeout_receipts_verified and v.exit_evidence_verified and v.export_completeness_independently_proven


def test_strict_retirement_gate_fails_if_receipt_confirms_more_parts_than_downloaded(tmp_path):
    from gpa.source_vault import vault_source_archive
    from gpa.transactions import sha256_file
    from gpa.takeout_receipt import create_takeout_receipt,write_takeout_receipt
    from gpa.verification import verify_migration
    import shutil
    a=tmp_path/'takeout-20260821T010203Z-001.zip';a.write_bytes(b'A');root=tmp_path/'archive';_minimal_verified_root_for_retirement(root,sha256_file(a))
    # Patch the run path so filename sequencing and receipt describe the same real source.
    rpath=root/'metadata/runs/r.json';r=json.loads(rpath.read_text());r['source_archives'][0]['path']=str(a);rpath.write_text(json.dumps(r))
    vault=tmp_path/'vault';vault_source_archive(a,vault);mirror=tmp_path/'mirror';shutil.copytree(root,mirror)
    receipt=tmp_path/'receipt.json';write_takeout_receipt(receipt,create_takeout_receipt([a],expected_part_count=2));exitp=_complete_exit_file(tmp_path/'exit.json')
    v=verify_migration(root,all_takeout_parts_confirmed=True,source_vault_root=vault,redundant_copy_root=mirror,takeout_receipts=[receipt],exit_evidence_path=exitp)
    assert not v.google_retirement_ready and not v.takeout_receipts_verified


def test_cli_can_create_takeout_receipt_and_exit_evidence(tmp_path,capsys):
    from gpa.__main__ import main
    a=tmp_path/'takeout-20260821T010203Z-001.zip';a.write_bytes(b'A')
    rp=tmp_path/'receipt.json';rc=main(['receipt','--input',str(a),'--output',str(rp),'--expected-count','1'])
    assert rc==0 and rp.exists();json.loads(capsys.readouterr().out)
    ep=tmp_path/'exit.json';rc=main(['exit-evidence','--output',str(ep),'--shared-albums-reviewed','--wanted-shared-media-secured','--partner-sharing-reviewed','--locked-folder-reviewed','--takeout-scope-reviewed','--recent-changes-accounted-for'])
    obj=json.loads(capsys.readouterr().out);assert rc==0 and ep.exists() and obj['recent_changes_accounted_for']


def test_movie_embedded_batch_opens_each_source_zip_once(tmp_path,monkeypatch):
    import zipfile as zf
    import gpa.video as video
    from gpa.embedded import FFprobeZipEmbeddedProvider
    from gpa.zipindex import ZipLibrary
    z=tmp_path/'movies.zip';mkzip(z,{f'D/v{i}.mov':b'not-real-video'+bytes([i]) for i in range(8)})
    lib=ZipLibrary([z]);members=[m for m in lib.scan() if m.suffix=='.mov']
    real=zf.ZipFile;opens=[]
    class CountingZipFile(real):
        def __init__(self,*a,**kw):opens.append(str(a[0]));super().__init__(*a,**kw)
    monkeypatch.setattr(zf,'ZipFile',CountingZipFile)
    monkeypatch.setattr(video,'probe_video',lambda p,ffprobe='ffprobe':{'format':{'tags':{}}})
    got=FFprobeZipEmbeddedProvider().batch(members,lib)
    assert len(got)==8 and len(opens)==1


def test_storage_independence_same_volume_cannot_be_overridden_by_confirmation(tmp_path):
    from gpa.storage_identity import assess_storage_independence
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    r=assess_storage_independence(a,b)
    assert r.same_volume is True and not r.separate_volume_proven and not r.separate_physical_media_confirmed
    r2=assess_storage_independence(a,b,separate_physical_media_confirmed=True)
    assert r2.schema=='gpa.storage-identity.v4'
    assert r2.same_volume is True
    assert r2.separate_physical_media_confirmation_requested
    assert r2.confirmation_source=='explicit_user_or_operator_input'
    assert not r2.separate_physical_media_confirmed
    assert r2.assessed_at_utc and r2.platform_system and r2.identity_method
    assert 'cannot override' in r2.note


def test_storage_independence_accepts_confirmation_only_without_contradictory_machine_evidence(tmp_path,monkeypatch):
    import gpa.storage_identity as sid
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    monkeypatch.setattr(sid.platform,'system',lambda:'Linux')
    monkeypatch.setattr(sid,'_device_token',lambda p: 'device-a' if Path(p)==a else 'device-b')
    r=sid.assess_storage_independence(a,b,separate_physical_media_confirmed=True)
    assert r.separate_volume_proven and r.same_volume is False
    assert r.separate_physical_media_confirmed


def test_retirement_gate_requires_separate_media_confirmation_even_for_hash_verified_copy(tmp_path):
    from gpa.source_vault import vault_source_archive
    from gpa.verification import verify_migration
    from gpa.transactions import sha256_file
    import shutil
    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw');digest=sha256_file(raw)
    root=tmp_path/'archive';_minimal_verified_root_for_retirement(root,digest)
    vault=tmp_path/'vault';vault_source_archive(raw,vault)
    mirror=tmp_path/'mirror';shutil.copytree(root,mirror)
    v=verify_migration(root,all_takeout_parts_confirmed=True,source_vault_root=vault,redundant_copy_root=mirror,require_source_vault_redundancy=False,require_takeout_receipt=False,require_exit_evidence=False)
    assert v.redundant_copy_verified and not v.google_retirement_ready
    assert any('separate physical media' in x for x in v.blockers)
    contradicted=verify_migration(root,all_takeout_parts_confirmed=True,source_vault_root=vault,redundant_copy_root=mirror,redundant_copy_separate_media_confirmed=True,require_source_vault_redundancy=False,require_takeout_receipt=False,require_exit_evidence=False)
    assert contradicted.schema=='gpa.verification.v4'
    assert contradicted.redundant_copy_verified
    assert contradicted.redundant_copy_separate_media_confirmed is False
    assert contradicted.redundant_copy_storage_identity['same_volume'] is True
    assert contradicted.redundant_copy_storage_identity['separate_physical_media_confirmation_requested'] is True
    assert contradicted.redundant_copy_storage_identity['separate_physical_media_confirmed'] is False
    assert not contradicted.google_retirement_ready
    assert any('separate physical media' in x for x in contradicted.blockers)


def test_retirement_gate_accepts_confirmation_when_distinct_volume_evidence_is_not_contradictory(tmp_path,monkeypatch):
    from gpa.source_vault import vault_source_archive
    from gpa.verification import verify_migration
    from gpa.transactions import sha256_file
    import gpa.storage_identity as sid
    monkeypatch.setattr(sid.platform,'system',lambda:'Linux')
    import shutil
    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw');digest=sha256_file(raw)
    root=tmp_path/'archive';_minimal_verified_root_for_retirement(root,digest)
    vault=tmp_path/'vault';vault_source_archive(raw,vault)
    mirror=tmp_path/'mirror';shutil.copytree(root,mirror)
    real=sid._device_token
    def fake_device(p):
        p=Path(p)
        try:
            if p.resolve().is_relative_to(mirror.resolve()):return 'backup-device'
            if p.resolve().is_relative_to(root.resolve()):return 'primary-device'
        except (OSError,ValueError):
            pass
        return real(p)
    monkeypatch.setattr(sid,'_device_token',fake_device)
    ok=verify_migration(root,all_takeout_parts_confirmed=True,source_vault_root=vault,redundant_copy_root=mirror,redundant_copy_separate_media_confirmed=True,require_source_vault_redundancy=False,require_takeout_receipt=False,require_exit_evidence=False)
    assert ok.redundant_copy_verified
    assert ok.redundant_copy_separate_volume_proven
    assert ok.redundant_copy_separate_media_confirmed
    assert ok.redundant_copy_storage_identity['schema']=='gpa.storage-identity.v4'
    assert ok.redundant_copy_storage_identity['primary_device']=='primary-device'
    assert ok.redundant_copy_storage_identity['secondary_device']=='backup-device'
    assert ok.google_retirement_ready



def test_retirement_gate_accepts_machine_proven_disjoint_windows_physical_disks(tmp_path,monkeypatch):
    from gpa.source_vault import vault_source_archive
    from gpa.verification import verify_migration
    from gpa.transactions import sha256_file
    from gpa.windows_storage import WindowsVolumeTopology, PhysicalDiskIdentity, BUS_SATA, BUS_NVME
    import gpa.storage_identity as sid
    import shutil

    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw');digest=sha256_file(raw)
    root=tmp_path/'archive';_minimal_verified_root_for_retirement(root,digest)
    vault=tmp_path/'vault';vault_source_archive(raw,vault)
    mirror=tmp_path/'mirror';shutil.copytree(root,mirror)

    monkeypatch.setattr(sid.platform,'system',lambda:'Windows')
    real=sid._device_token
    def fake_device(p):
        rp=Path(p).resolve()
        if rp==root.resolve():return 'volume-primary'
        if rp==mirror.resolve():return 'volume-backup'
        return real(p)
    monkeypatch.setattr(sid,'_device_token',fake_device)
    def fake_topology(p):
        rp=Path(p).resolve()
        if rp==root.resolve():
            ident=PhysicalDiskIdentity(disk_number=2,bus_type=BUS_SATA,bus_type_name='sata',serial_number='PRIMARY-2',descriptor_supported=True,hardware_identity_eligible=True)
            return WindowsVolumeTopology(path=str(p),topology_supported=True,local_volume=True,disk_numbers=(2,),disk_identities=(ident,),hardware_identity_supported=True)
        if rp==mirror.resolve():
            ident=PhysicalDiskIdentity(disk_number=5,bus_type=BUS_NVME,bus_type_name='nvme',serial_number='BACKUP-5',descriptor_supported=True,hardware_identity_eligible=True)
            return WindowsVolumeTopology(path=str(p),topology_supported=True,local_volume=True,disk_numbers=(5,),disk_identities=(ident,),hardware_identity_supported=True)
        return WindowsVolumeTopology(path=str(p),error='not part of this test')
    monkeypatch.setattr(sid,'_windows_volume_topology',fake_topology)

    v=verify_migration(
        root,all_takeout_parts_confirmed=True,source_vault_root=vault,
        redundant_copy_root=mirror,require_source_vault_redundancy=False,
        require_takeout_receipt=False,require_exit_evidence=False,
    )
    assert v.redundant_copy_verified
    assert v.redundant_copy_separate_volume_proven
    assert v.redundant_copy_separate_physical_device_proven
    assert v.redundant_copy_separate_media_confirmed
    assert v.redundant_copy_storage_identity['independence_evidence_source']=='windows_disk_extents_and_storage_device_descriptor'
    assert not v.redundant_copy_storage_identity['separate_physical_media_confirmation_requested']
    assert v.google_retirement_ready


def test_current_google_photos_video_extensions_are_classified_as_media(tmp_path):
    from gpa.formats import VIDEO_SUFFIXES
    from gpa.planner import LibraryPlanner
    # Current Google Photos help documents these families; .mpeg is accepted as a
    # conservative synonym for .mpg even though Google's list spells out .mpg.
    documented={'.mpg','.mod','.mmv','.tod','.wmv','.asf','.avi','.divx','.mov','.m4v','.3gp','.3g2','.mp4','.m2t','.m2ts','.mts','.mkv'}
    assert documented <= VIDEO_SUFFIXES
    members={f'D/v{i}{ext}':f'v{i}'.encode() for i,ext in enumerate(sorted(documented))}
    z=tmp_path/'takeout.zip';mkzip(z,members)
    r=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan_report()
    assert len(r.assets)==len(documented)
    assert not r.unclassified


def test_common_google_supported_raw_extensions_are_preserved_as_media(tmp_path):
    from gpa.formats import RAW_SUFFIXES
    from gpa.planner import LibraryPlanner
    wanted={'.dng','.cr2','.cr3','.nef','.arw','.raf','.rw2','.orf','.pef','.srw'}
    assert wanted <= RAW_SUFFIXES
    z=tmp_path/'takeout.zip';mkzip(z,{f'D/r{i}{ext}':f'raw{i}'.encode() for i,ext in enumerate(sorted(wanted))})
    r=LibraryPlanner([z],embedded_provider=lambda m,l: None).plan_report()
    assert len(r.assets)==len(wanted) and not r.unclassified


def test_retirement_gate_rejects_same_volume_source_vault_confirmation(tmp_path,monkeypatch):
    from gpa.source_vault import vault_source_archive
    from gpa.verification import verify_migration
    from gpa.transactions import sha256_file
    import gpa.storage_identity as sid
    monkeypatch.setattr(sid.platform,'system',lambda:'Linux')
    import shutil
    raw=tmp_path/'takeout.zip';raw.write_bytes(b'raw');digest=sha256_file(raw)
    root=tmp_path/'archive';_minimal_verified_root_for_retirement(root,digest)
    vault=tmp_path/'vault';vault_source_archive(raw,vault)
    vaultmirror=tmp_path/'vaultmirror';shutil.copytree(vault,vaultmirror)
    mirror=tmp_path/'mirror';shutil.copytree(root,mirror)
    # Simulate a genuinely different archive-backup volume, while the source
    # vault and its redundant copy remain on the same detected volume.
    real=sid._device_token
    def fake_device(p):
        rp=Path(p).resolve()
        if rp==root.resolve():return 'archive-primary'
        if rp==mirror.resolve():return 'archive-backup'
        return real(p)
    monkeypatch.setattr(sid,'_device_token',fake_device)
    v=verify_migration(
        root,all_takeout_parts_confirmed=True,
        source_vault_root=vault,source_vault_redundant_copy_root=vaultmirror,
        redundant_copy_root=mirror,redundant_copy_separate_media_confirmed=True,
        source_vault_redundant_separate_media_confirmed=True,
        require_takeout_receipt=False,require_exit_evidence=False,
    )
    assert v.redundant_copy_separate_media_confirmed
    assert v.source_vault_redundant_verified
    assert v.source_vault_redundant_storage_identity['same_volume'] is True
    assert v.source_vault_redundant_storage_identity['separate_physical_media_confirmation_requested'] is True
    assert v.source_vault_redundant_separate_media_confirmed is False
    assert not v.google_retirement_ready
    assert any('redundant raw-source-vault copy' in b for b in v.blockers)


def test_source_vault_crash_after_bytes_commit_heals_on_rerun_reconciled(tmp_path):
    from gpa.source_vault import vault_source_archive,audit_source_vault
    z=tmp_path/'takeout.zip';mkzip(z,{'Photos/a.jpg':b'A','Photos/a.jpg.json':gjson('a.jpg')})
    before=z.read_bytes();vault=tmp_path/'vault';crashed=[]
    def fail(dest):
        crashed.append(dest);raise RuntimeError('simulated power loss after source-vault byte commit')
    with pytest.raises(RuntimeError,match='simulated power loss'):
        vault_source_archive(z,vault,after_bytes_commit=fail)
    assert z.read_bytes()==before and len(crashed)==1 and crashed[0].read_bytes()==before
    assert not (vault/'records').exists()
    row=vault_source_archive(z,vault)
    assert row.reused_existing_bytes and (vault/row.vaulted_path).read_bytes()==before
    audit=audit_source_vault(vault)
    assert audit.ok and audit.records_checked==1 and audit.archives_checked==1


def test_verified_mirror_crash_resumes_without_overwrite_reconciled(tmp_path):
    from gpa.mirror import build_tree_manifest,materialize_verified_mirror,verify_mirror
    src=tmp_path/'source';src.mkdir();(src/'a.bin').write_bytes(b'A');(src/'nested').mkdir();(src/'nested'/'b.bin').write_bytes(b'B')
    manifest=build_tree_manifest(src);dst=tmp_path/'mirror'
    def fail(n,path):
        if n==1:raise RuntimeError('simulated mirror interruption')
    with pytest.raises(RuntimeError,match='simulated mirror interruption'):
        materialize_verified_mirror(manifest,src,dst,after_commit=fail)
    assert sum(1 for p in dst.rglob('*') if p.is_file())==1
    report=materialize_verified_mirror(manifest,src,dst)
    assert report.ok and report.files_expected==2 and report.files_reused==1 and report.files_copied==1
    assert verify_mirror(manifest,dst).ok


def test_verified_mirror_full_preflight_blocks_foreign_destination_before_copy_reconciled(tmp_path):
    from gpa.mirror import build_tree_manifest,materialize_verified_mirror
    src=tmp_path/'source';src.mkdir();(src/'a').write_bytes(b'A');(src/'z').write_bytes(b'Z')
    manifest=build_tree_manifest(src);dst=tmp_path/'mirror';dst.mkdir();(dst/'z').write_bytes(b'FOREIGN')
    with pytest.raises(RuntimeError,match='destination conflict'):
        materialize_verified_mirror(manifest,src,dst)
    assert not (dst/'a').exists() and (dst/'z').read_bytes()==b'FOREIGN'


def test_tree_manifest_rejects_symlinks_reconciled(tmp_path):
    from gpa.mirror import build_tree_manifest
    src=tmp_path/'source';src.mkdir();outside=tmp_path/'outside';outside.write_bytes(b'outside')
    link=src/'link'
    try:link.symlink_to(outside)
    except (OSError,NotImplementedError):pytest.skip('symlink creation unavailable')
    with pytest.raises(RuntimeError,match='symlink|junction'):
        build_tree_manifest(src)


def test_reproducible_source_vault_and_mirror_fault_recovery_tool_smoke_reconciled(tmp_path):
    import subprocess,sys
    tool=Path(__file__).resolve().parents[1]/'tools'/'stress_backup_recovery.py'
    report=tmp_path/'backup-fault-report.json';work=tmp_path/'backup-fault-stress'
    cp=subprocess.run([sys.executable,str(tool),'--workdir',str(work),'--sources','4','--archive-files','8','--report',str(report)],capture_output=True,text=True,timeout=60)
    assert cp.returncode==0,cp.stdout+cp.stderr
    obj=json.loads(report.read_text())
    assert obj['passed'] and obj['source_vault']['records']==4 and obj['source_vault']['archives']==4
    assert obj['checks']['source_vault_fault_reproduced'] and obj['checks']['source_vault_rerun_reused_crashed_bytes']
    assert obj['checks']['source_vault_mirror_fault_reproduced'] and obj['checks']['source_vault_mirror_resumed']
    assert obj['checks']['archive_mirror_fault_reproduced'] and obj['checks']['archive_mirror_resumed']
    assert obj['checks']['raw_takeout_originals_unchanged'] and obj['checks']['no_staging_files_left']


def test_complete_suite_registry_covers_every_release_test_module():
    from tools.run_complete_suite import MODULES
    root=Path(__file__).resolve().parents[1]
    actual={p.relative_to(root).as_posix() for p in (root/'tests').glob('test_*.py')}
    assert set(MODULES)==actual
    assert len(MODULES)==len(actual)


def test_stage_bytes_disk_full_during_fsync_cleans_staging(tmp_path, monkeypatch):
    import errno, os
    from gpa.transactions import stage_bytes
    real_fsync=os.fsync
    def fail_fsync(fd):
        raise OSError(errno.ENOSPC,'simulated full disk')
    monkeypatch.setattr(os,'fsync',fail_fsync)
    with pytest.raises(OSError) as exc:
        stage_bytes(b'irreplaceable-sidecar',tmp_path)
    assert exc.value.errno==errno.ENOSPC
    assert not list(tmp_path.glob('.gpa-stage-*'))
    monkeypatch.setattr(os,'fsync',real_fsync)


def test_commit_no_overwrite_disk_full_removes_partial_destination_but_keeps_stage(tmp_path, monkeypatch):
    import errno, os, shutil
    from gpa.transactions import commit_no_overwrite, CommitError
    staged=tmp_path/'.gpa-stage-test';staged.write_bytes(b'IRREPLACEABLE-STAGED-BYTES')
    dest=tmp_path/'final.bin'
    def force_fallback(src,dst):
        raise OSError(errno.EXDEV,'simulated cross-device hardlink')
    def partial_then_full(inp,out,*args,**kwargs):
        out.write(b'PARTIAL')
        raise OSError(errno.ENOSPC,'simulated full disk')
    monkeypatch.setattr(os,'link',force_fallback)
    monkeypatch.setattr(shutil,'copyfileobj',partial_then_full)
    with pytest.raises(CommitError,match='commit failed after hardlink fallback'):
        commit_no_overwrite(staged,dest)
    assert not dest.exists()
    assert staged.read_bytes()==b'IRREPLACEABLE-STAGED-BYTES'


def test_fsync_dir_propagates_real_storage_failure(tmp_path, monkeypatch):
    import errno, os
    from gpa.transactions import fsync_dir
    probe=tmp_path/'.fsync-dir-probe';probe.write_bytes(b'')
    fd=os.open(probe,os.O_RDONLY)
    monkeypatch.setattr(os,'open',lambda *args,**kwargs:fd)
    monkeypatch.setattr(os,'fsync',lambda fd: (_ for _ in ()).throw(OSError(errno.ENOSPC,'simulated full disk')))
    with pytest.raises(OSError) as exc:
        fsync_dir(tmp_path)
    assert exc.value.errno==errno.ENOSPC


def test_fsync_dir_ignores_unsupported_operation(tmp_path, monkeypatch):
    import errno, os
    from gpa.transactions import fsync_dir
    monkeypatch.setattr(os,'fsync',lambda fd: (_ for _ in ()).throw(OSError(errno.EINVAL,'directory fsync unsupported')))
    fsync_dir(tmp_path)


def test_stage_copy_disk_full_during_file_fsync_cleans_staging(tmp_path, monkeypatch):
    import errno, os, hashlib
    from gpa.transactions import stage_copy
    src=tmp_path/'source.bin';src.write_bytes(b'PRESERVATION-SOURCE')
    expected=hashlib.sha256(src.read_bytes()).hexdigest()
    monkeypatch.setattr(os,'fsync',lambda fd: (_ for _ in ()).throw(OSError(errno.ENOSPC,'simulated full disk')))
    with pytest.raises(OSError) as exc:
        stage_copy(src,tmp_path/'dest',expected)
    assert exc.value.errno==errno.ENOSPC
    assert src.read_bytes()==b'PRESERVATION-SOURCE'
    assert not list((tmp_path/'dest').glob('.gpa-stage-*'))


def test_commit_hardlink_dir_fsync_disk_full_rolls_back_dest_and_keeps_stage(tmp_path, monkeypatch):
    import errno
    import gpa.transactions as transactions
    from gpa.transactions import commit_no_overwrite, CommitError
    staged=tmp_path/'.gpa-stage-test';staged.write_bytes(b'RECOVERABLE-STAGE')
    dest=tmp_path/'final.bin';calls={'n':0}
    def fail_first_dir_fsync(_path):
        calls['n']+=1
        raise OSError(errno.ENOSPC,'simulated namespace durability failure')
    monkeypatch.setattr(transactions,'fsync_dir',fail_first_dir_fsync)
    with pytest.raises(CommitError,match='durability confirmation failed'):
        commit_no_overwrite(staged,dest)
    assert staged.read_bytes()==b'RECOVERABLE-STAGE'
    assert not dest.exists()


def test_commit_hardlink_enospc_does_not_attempt_copy_fallback(tmp_path, monkeypatch):
    import errno, os
    from gpa.transactions import commit_no_overwrite, CommitError
    staged=tmp_path/'.gpa-stage-test';staged.write_bytes(b'RECOVERABLE-STAGE')
    dest=tmp_path/'final.bin';opened=[]
    def full_link(src,dst):raise OSError(errno.ENOSPC,'simulated full disk at link')
    real_open=os.open
    def track_open(path,flags,*args,**kwargs):
        if Path(path)==dest:opened.append(path)
        return real_open(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'link',full_link);monkeypatch.setattr(os,'open',track_open)
    with pytest.raises(CommitError,match='hardlink commit failed'):
        commit_no_overwrite(staged,dest)
    assert opened==[]
    assert staged.read_bytes()==b'RECOVERABLE-STAGE'
    assert not dest.exists()


def test_revision_pointer_replace_failure_keeps_old_pointer_and_cleans_stage(tmp_path, monkeypatch):
    import errno, os
    from gpa.revisions import RevisionStore
    store=RevisionStore(tmp_path/'archive')
    _,rid1=store.prepare_revision('asset',{'generation':1});store.set_current('asset',rid1)
    _,rid2=store.prepare_revision('asset',{'generation':2})
    real_replace=os.replace
    def fail_replace(src,dst):raise OSError(errno.EIO,'simulated pointer replace failure')
    monkeypatch.setattr(os,'replace',fail_replace)
    with pytest.raises(OSError) as exc:store.set_current('asset',rid2)
    assert exc.value.errno==errno.EIO
    assert store.current('asset')==rid1
    asset_dir=tmp_path/'archive'/'metadata'/'assets'/'asset'
    assert not list(asset_dir.glob('.gpa-stage-*'))
    monkeypatch.setattr(os,'replace',real_replace)
    store.set_current('asset',rid2)
    assert store.current('asset')==rid2


def test_revision_pointer_dir_fsync_failure_is_recoverable_without_stage_leak(tmp_path, monkeypatch):
    import errno
    import gpa.revisions as revisions
    from gpa.revisions import RevisionStore
    store=RevisionStore(tmp_path/'archive')
    _,rid1=store.prepare_revision('asset',{'generation':1});store.set_current('asset',rid1)
    _,rid2=store.prepare_revision('asset',{'generation':2})
    real_fsync_dir=revisions.fsync_dir
    calls={'n':0}
    def fail_once(path):
        calls['n']+=1
        if calls['n']==1:raise OSError(errno.ENOSPC,'simulated pointer namespace durability failure')
        return real_fsync_dir(path)
    monkeypatch.setattr(revisions,'fsync_dir',fail_once)
    with pytest.raises(OSError) as exc:store.set_current('asset',rid2)
    assert exc.value.errno==errno.ENOSPC
    asset_dir=tmp_path/'archive'/'metadata'/'assets'/'asset'
    assert not list(asset_dir.glob('.gpa-stage-*'))
    # os.replace completed before the durability error. The pointer therefore may
    # already be the new valid revision; rerun must be harmless and deterministic.
    assert store.current('asset')==rid2
    store.set_current('asset',rid2)
    assert store.current('asset')==rid2


def test_source_vault_commit_storage_failure_cleans_derived_stage(tmp_path, monkeypatch):
    import errno, os
    from gpa.source_vault import vault_source_archive
    from gpa.transactions import CommitError
    src=tmp_path/'takeout.zip';src.write_bytes(b'RAW-TAKEOUT-BYTES')
    vault=tmp_path/'vault'
    def fail_link(a,b):raise OSError(errno.ENOSPC,'simulated full destination')
    monkeypatch.setattr(os,'link',fail_link)
    with pytest.raises(CommitError,match='hardlink commit failed'):
        vault_source_archive(src,vault)
    assert src.read_bytes()==b'RAW-TAKEOUT-BYTES'
    assert not list(vault.rglob('.gpa-stage-*'))
    assert not list((vault/'records').glob('*.json')) if (vault/'records').exists() else True


def test_mirror_commit_storage_failure_cleans_derived_stage_and_keeps_source(tmp_path, monkeypatch):
    import errno, os
    from gpa.mirror import build_tree_manifest,materialize_verified_mirror
    from gpa.transactions import CommitError
    src=tmp_path/'source';src.mkdir();(src/'a.bin').write_bytes(b'A'*32)
    manifest=build_tree_manifest(src);dst=tmp_path/'mirror'
    def fail_link(a,b):raise OSError(errno.ENOSPC,'simulated full destination')
    monkeypatch.setattr(os,'link',fail_link)
    with pytest.raises(CommitError,match='hardlink commit failed'):
        materialize_verified_mirror(manifest,src,dst)
    assert (src/'a.bin').read_bytes()==b'A'*32
    assert not list(dst.rglob('.gpa-stage-*')) if dst.exists() else True
    assert not (dst/'a.bin').exists()


def test_mirror_stage_copy_device_loss_cleans_partial_stage(tmp_path, monkeypatch):
    import errno, shutil
    from gpa.mirror import build_tree_manifest,materialize_verified_mirror
    src=tmp_path/'source';src.mkdir();(src/'a.bin').write_bytes(b'A'*64)
    manifest=build_tree_manifest(src);dst=tmp_path/'mirror'
    def partial_then_eio(source,target,*args,**kwargs):
        target=Path(target);target.write_bytes(b'PARTIAL')
        raise OSError(errno.EIO,'simulated device removal during copy')
    monkeypatch.setattr(shutil,'copyfile',partial_then_eio)
    with pytest.raises(OSError) as exc:
        materialize_verified_mirror(manifest,src,dst)
    assert exc.value.errno==errno.EIO
    assert not list(dst.rglob('.gpa-stage-*')) if dst.exists() else True
    assert (src/'a.bin').read_bytes()==b'A'*64


def test_source_vault_new_object_verification_failure_removes_unrecorded_dest(tmp_path, monkeypatch):
    import gpa.source_vault as sv
    src=tmp_path/'takeout.zip';src.write_bytes(b'RAW-TAKEOUT-BYTES')
    vault=tmp_path/'vault';real_sha=sv.sha256_file;calls={'n':0}
    def wrong_after_source(path):
        calls['n']+=1
        if Path(path)==src:return real_sha(path)
        return '0'*64
    monkeypatch.setattr(sv,'sha256_file',wrong_after_source)
    with pytest.raises(RuntimeError,match='source-vault verification failed'):
        sv.vault_source_archive(src,vault)
    assert src.read_bytes()==b'RAW-TAKEOUT-BYTES'
    assert not list((vault/'archives').rglob('*.zip')) if (vault/'archives').exists() else True
    assert not list((vault/'records').glob('*.json')) if (vault/'records').exists() else True


def test_mirror_new_destination_verification_failure_removes_unverified_copy(tmp_path, monkeypatch):
    import gpa.mirror as mir
    src=tmp_path/'source';src.mkdir();(src/'a.bin').write_bytes(b'A'*32)
    manifest=mir.build_tree_manifest(src);dst=tmp_path/'mirror';real_sha=mir.file_sha256
    calls={'dest':0}
    def wrong_dest(path):
        path=Path(path)
        if path.is_relative_to(dst):
            calls['dest']+=1
            return '0'*64
        return real_sha(path)
    monkeypatch.setattr(mir,'file_sha256',wrong_dest)
    with pytest.raises(mir.MirrorConflict,match='verification failed after commit'):
        mir.materialize_verified_mirror(manifest,src,dst)
    assert (src/'a.bin').read_bytes()==b'A'*32
    assert not (dst/'a.bin').exists()
    assert not list(dst.rglob('.gpa-stage-*')) if dst.exists() else True


def test_write_json_new_dir_fsync_failure_preserves_primary_error_and_cleans_record(tmp_path, monkeypatch):
    import errno
    import gpa.transactions as tx
    path=tmp_path/'records'/'r.json'
    calls={'n':0}
    def fail_fsync_dir(p):
        calls['n']+=1
        if calls['n']==1:
            raise OSError(errno.ENOSPC,'primary namespace durability failure')
        raise OSError(errno.EIO,'cleanup durability failure')
    monkeypatch.setattr(tx,'fsync_dir',fail_fsync_dir)
    with pytest.raises(OSError) as exc:
        tx.write_json_new(path,{'x':1})
    assert exc.value.errno==errno.ENOSPC
    assert not path.exists()


def test_source_vault_record_dir_fsync_failure_leaves_bytes_reusable_and_retryable(tmp_path, monkeypatch):
    import errno
    import gpa.transactions as tx
    import gpa.source_vault as sv
    src=tmp_path/'takeout.zip';src.write_bytes(b'RAW-TAKEOUT-BYTES')
    vault=tmp_path/'vault';real_fsync_dir=tx.fsync_dir;calls={'n':0}
    def fail_record_dir_once(path):
        path=Path(path)
        if path.name=='records' and calls['n']==0:
            calls['n']+=1
            raise OSError(errno.EIO,'simulated record directory loss')
        return real_fsync_dir(path)
    monkeypatch.setattr(tx,'fsync_dir',fail_record_dir_once)
    with pytest.raises(OSError) as exc:
        sv.vault_source_archive(src,vault)
    assert exc.value.errno==errno.EIO
    assert not list((vault/'records').glob('*.json')) if (vault/'records').exists() else True
    objects=list((vault/'archives').rglob('*.zip'))
    assert len(objects)==1 and objects[0].read_bytes()==b'RAW-TAKEOUT-BYTES'
    monkeypatch.setattr(tx,'fsync_dir',real_fsync_dir)
    out=sv.vault_source_archive(src,vault)
    assert out.reused_existing_bytes is True
    assert (vault/out.record_path).is_file()
    assert sv.audit_source_vault(vault).ok


def test_source_vault_audit_reports_archive_read_eio_instead_of_crashing(tmp_path, monkeypatch):
    import errno
    import gpa.source_vault as sv
    src=tmp_path/'takeout.zip';src.write_bytes(b'RAW-TAKEOUT-BYTES')
    vault=tmp_path/'vault';out=sv.vault_source_archive(src,vault)
    real_sha=sv.sha256_file
    def fail_vault_read(path):
        path=Path(path)
        if path.is_relative_to(vault/'archives'):
            raise OSError(errno.EIO,'simulated device read failure')
        return real_sha(path)
    monkeypatch.setattr(sv,'sha256_file',fail_vault_read)
    audit=sv.audit_source_vault(vault)
    assert not audit.ok
    assert audit.records_checked==1
    assert audit.archives_checked==0
    assert any('vaulted archive unreadable' in p and 'device read failure' in p for p in audit.problems)


def test_mirror_verify_reports_read_eio_as_mismatch_instead_of_crashing(tmp_path, monkeypatch):
    import errno
    import gpa.mirror as mir
    src=tmp_path/'source';src.mkdir();(src/'a.bin').write_bytes(b'A'*64)
    manifest=mir.build_tree_manifest(src);dst=tmp_path/'mirror'
    mir.materialize_verified_mirror(manifest,src,dst)
    real_sha=mir.file_sha256
    def fail_mirror_read(path):
        path=Path(path)
        if path.is_relative_to(dst):
            raise OSError(errno.EIO,'simulated mirror read failure')
        return real_sha(path)
    monkeypatch.setattr(mir,'file_sha256',fail_mirror_read)
    report=mir.verify_mirror(manifest,dst)
    assert not report.ok
    assert report.files_checked==1
    assert report.mismatched==['a.bin']


def test_source_vault_audit_reports_records_directory_enumeration_eio(tmp_path, monkeypatch):
    import errno
    import pathlib
    import gpa.source_vault as sv
    src=tmp_path/'takeout.zip';src.write_bytes(b'RAW')
    vault=tmp_path/'vault';sv.vault_source_archive(src,vault)
    real_glob=pathlib.Path.glob
    def fail_records_glob(self,pattern):
        if self==vault/'records' and pattern=='*.json':
            raise OSError(errno.EIO,'simulated records directory read failure')
        return real_glob(self,pattern)
    monkeypatch.setattr(pathlib.Path,'glob',fail_records_glob)
    audit=sv.audit_source_vault(vault)
    assert not audit.ok
    assert any('records directory unreadable' in p for p in audit.problems)


def test_source_vault_audit_reports_archives_directory_enumeration_eio(tmp_path, monkeypatch):
    import errno
    import pathlib
    import gpa.source_vault as sv
    src=tmp_path/'takeout.zip';src.write_bytes(b'RAW')
    vault=tmp_path/'vault';sv.vault_source_archive(src,vault)
    real_rglob=pathlib.Path.rglob
    def fail_archives_rglob(self,pattern):
        if self==vault/'archives' and pattern=='*':
            raise OSError(errno.EIO,'simulated archives directory read failure')
        return real_rglob(self,pattern)
    monkeypatch.setattr(pathlib.Path,'rglob',fail_archives_rglob)
    audit=sv.audit_source_vault(vault)
    assert not audit.ok
    assert audit.records_checked==1 and audit.archives_checked==1
    assert any('archives directory unreadable' in p for p in audit.problems)


def _archive_for_enumeration_fault(tmp_path):
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    result=RunExecutor([z],root).execute(run_id='r')
    aid=result['successes'][0]['asset_id'];ad=root/'metadata'/'assets'/aid
    (ad/'blobs').mkdir(exist_ok=True)
    (ad/'identity'/'observations').mkdir(parents=True,exist_ok=True)
    (ad/'identity'/'sightings').mkdir(parents=True,exist_ok=True)
    return root,ad


def test_archive_audit_reports_assets_directory_enumeration_eio(tmp_path, monkeypatch):
    import errno, pathlib
    root,_=_archive_for_enumeration_fault(tmp_path);assets=root/'metadata'/'assets';real=pathlib.Path.iterdir
    def fail(self):
        if self==assets:raise OSError(errno.EIO,'simulated assets directory read failure')
        return real(self)
    monkeypatch.setattr(pathlib.Path,'iterdir',fail)
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_assets_directory' for x in report.problems)


def test_archive_audit_reports_revisions_directory_enumeration_eio(tmp_path, monkeypatch):
    import errno, pathlib
    root,ad=_archive_for_enumeration_fault(tmp_path);revisions=ad/'revisions';real=pathlib.Path.glob
    def fail(self,pattern,**kwargs):
        if self==revisions and pattern=='*.json':raise OSError(errno.EIO,'simulated revisions directory read failure')
        return real(self,pattern,**kwargs)
    monkeypatch.setattr(pathlib.Path,'glob',fail)
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_revisions_directory' for x in report.problems)


def test_archive_audit_reports_blobs_directory_enumeration_eio(tmp_path, monkeypatch):
    import errno, pathlib
    root,ad=_archive_for_enumeration_fault(tmp_path);blobs=ad/'blobs';real=pathlib.Path.rglob
    def fail(self,pattern,**kwargs):
        if self==blobs and pattern=='*':raise OSError(errno.EIO,'simulated blobs directory read failure')
        return real(self,pattern,**kwargs)
    monkeypatch.setattr(pathlib.Path,'rglob',fail)
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_blobs_directory' for x in report.problems)


def test_archive_audit_reports_identity_observations_enumeration_eio(tmp_path, monkeypatch):
    import errno, pathlib
    root,ad=_archive_for_enumeration_fault(tmp_path);observations=ad/'identity'/'observations';real=pathlib.Path.glob
    def fail(self,pattern,**kwargs):
        if self==observations and pattern=='*.json':raise OSError(errno.EIO,'simulated observations directory read failure')
        return real(self,pattern,**kwargs)
    monkeypatch.setattr(pathlib.Path,'glob',fail)
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_identity_observations_directory' for x in report.problems)


def test_archive_audit_reports_identity_sightings_enumeration_eio(tmp_path, monkeypatch):
    import errno, pathlib
    root,ad=_archive_for_enumeration_fault(tmp_path);sightings=ad/'identity'/'sightings';real=pathlib.Path.glob
    def fail(self,pattern,**kwargs):
        if self==sightings and pattern=='*.json':raise OSError(errno.EIO,'simulated sightings directory read failure')
        return real(self,pattern,**kwargs)
    monkeypatch.setattr(pathlib.Path,'glob',fail)
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_identity_sightings_directory' for x in report.problems)


def test_archive_audit_reports_runs_directory_enumeration_eio(tmp_path, monkeypatch):
    import errno, pathlib
    root,_=_archive_for_enumeration_fault(tmp_path);runs=root/'metadata'/'runs';real=pathlib.Path.glob
    def fail(self,pattern,**kwargs):
        if self==runs and pattern=='*.json':raise OSError(errno.EIO,'simulated runs directory read failure')
        return real(self,pattern,**kwargs)
    monkeypatch.setattr(pathlib.Path,'glob',fail)
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_runs_directory' for x in report.problems)


def test_archive_audit_reports_media_hash_read_eio(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    result=RunExecutor([z],root).execute(run_id='r');media=(root/result['successes'][0]['media']).resolve();real=ga.sha256_file
    def fail(path):
        if Path(path).resolve()==media:raise OSError(errno.EIO,'simulated media read failure')
        return real(path)
    monkeypatch.setattr(ga,'sha256_file',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_media' for x in report.problems)


def test_archive_audit_reports_xmp_hash_read_eio(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive'
    mkzip(z,{'D/a.jpg':b'MEDIA','D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2020,1,15,12),'caption')})
    result=RunExecutor([z],root).execute(run_id='r');xp=(root/result['successes'][0]['xmp']).resolve();real=ga.sha256_file
    def fail(path):
        if Path(path).resolve()==xp:raise OSError(errno.EIO,'simulated xmp read failure')
        return real(path)
    monkeypatch.setattr(ga,'sha256_file',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_xmp' for x in report.problems)


def test_archive_audit_reports_blob_hash_read_eio(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z1=tmp_path/'one.zip';z2=tmp_path/'two.zip';root=tmp_path/'archive';dt=datetime(2020,1,15,12)
    mkzip(z1,{'D/a.jpg':b'MEDIA','D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'old')});RunExecutor([z1],root).execute(run_id='r1')
    mkzip(z2,{'D/a.jpg':b'MEDIA','D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'new')});RunExecutor([z2],root).execute(run_id='r2')
    blob=next(x for x in (root/'metadata'/'assets').rglob('*') if 'blobs' in x.parts and x.is_file()).resolve();real=ga.sha256_file
    def fail(path):
        if Path(path).resolve()==blob:raise OSError(errno.EIO,'simulated blob read failure')
        return real(path)
    monkeypatch.setattr(ga,'sha256_file',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_blob' for x in report.problems)


def test_archive_audit_reports_unclassified_hash_read_eio(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA','D/future.xyz':b'UNKNOWN'})
    result=RunExecutor([z],root).execute(run_id='r');saved=(root/result['unclassified_preserved'][0]['saved_as']).resolve();real=ga.sha256_file
    def fail(path):
        if Path(path).resolve()==saved:raise OSError(errno.EIO,'simulated unclassified read failure')
        return real(path)
    monkeypatch.setattr(ga,'sha256_file',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_unclassified' for x in report.problems)


def test_checked_exists_distinguishes_missing_from_stat_eio(tmp_path, monkeypatch):
    import errno, pathlib
    import gpa.audit as ga
    missing=tmp_path/'missing';exists,err=ga._checked_exists(missing)
    assert exists is False and err is None
    target=tmp_path/'device';target.write_bytes(b'x');real=pathlib.Path.stat
    def fail(self,*args,**kwargs):
        if self==target:raise OSError(errno.EIO,'simulated stat failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'stat',fail)
    exists,err=ga._checked_exists(target)
    assert exists is None and isinstance(err,OSError) and err.errno==errno.EIO


def test_archive_audit_reports_media_stat_eio(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    result=RunExecutor([z],root).execute(run_id='r');media=(root/result['successes'][0]['media']).resolve();real=ga._checked_exists
    def fail(path):
        if Path(path).resolve()==media:return None,OSError(errno.EIO,'simulated media stat failure')
        return real(path)
    monkeypatch.setattr(ga,'_checked_exists',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_media' and 'stat failure' in x.message for x in report.problems)
    assert not any(x.kind=='missing_media' for x in report.problems)


def test_archive_audit_reports_current_revision_stat_eio(tmp_path, monkeypatch):
    import errno, json
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    result=RunExecutor([z],root).execute(run_id='r');aid=result['successes'][0]['asset_id'];ad=root/'metadata'/'assets'/aid
    rid=json.loads((ad/'current.json').read_text())['revision_id'];rp=(ad/'revisions'/f'{rid}.json').resolve();real=ga._checked_exists
    def fail(path):
        if Path(path).resolve()==rp:return None,OSError(errno.EIO,'simulated revision stat failure')
        return real(path)
    monkeypatch.setattr(ga,'_checked_exists',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_current_revision' for x in report.problems)
    assert not any(x.kind=='missing_current_revision' for x in report.problems)


def test_archive_audit_reports_runs_directory_stat_eio(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA','D/future.xyz':b'UNKNOWN'})
    RunExecutor([z],root).execute(run_id='r');runs=(root/'metadata'/'runs').resolve();real=ga._checked_exists
    def fail(path):
        if Path(path).resolve()==runs:return None,OSError(errno.EIO,'simulated runs stat failure')
        return real(path)
    monkeypatch.setattr(ga,'_checked_exists',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_runs_directory' for x in report.problems)


def test_archive_audit_reports_unclassified_stat_eio_not_missing(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA','D/future.xyz':b'UNKNOWN'})
    result=RunExecutor([z],root).execute(run_id='r');saved=(root/result['unclassified_preserved'][0]['saved_as']).resolve();real=ga._checked_exists
    def fail(path):
        if Path(path).resolve()==saved:return None,OSError(errno.EIO,'simulated unclassified stat failure')
        return real(path)
    monkeypatch.setattr(ga,'_checked_exists',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_unclassified' for x in report.problems)
    assert not any(x.kind=='missing_unclassified' for x in report.problems)


def test_checked_kind_surfaces_stat_eio_instead_of_false(tmp_path, monkeypatch):
    import errno, pathlib
    import gpa.audit as ga
    target=tmp_path/'entry';target.mkdir();real=pathlib.Path.stat
    def fail(self,*args,**kwargs):
        if self==target:raise OSError(errno.EIO,'simulated kind stat failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'stat',fail)
    value,err=ga._checked_kind(target,directory=True)
    assert value is None and isinstance(err,OSError) and err.errno==errno.EIO


def test_archive_audit_reports_asset_entry_stat_eio_not_silent_skip(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    result=RunExecutor([z],root).execute(run_id='r');ad=(root/'metadata'/'assets'/result['successes'][0]['asset_id']).resolve();real=ga._checked_kind
    def fail(path,*,directory):
        if Path(path).resolve()==ad and directory:return None,OSError(errno.EIO,'simulated asset entry stat failure')
        return real(path,directory=directory)
    monkeypatch.setattr(ga,'_checked_kind',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_asset_entry' for x in report.problems)
    assert report.assets_checked==0


def test_archive_audit_reports_blob_entry_stat_eio_not_silent_skip(tmp_path, monkeypatch):
    import errno
    import gpa.audit as ga
    z1=tmp_path/'one.zip';z2=tmp_path/'two.zip';root=tmp_path/'archive';dt=datetime(2020,1,15,12)
    mkzip(z1,{'D/a.jpg':b'MEDIA','D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'old')});RunExecutor([z1],root).execute(run_id='r1')
    mkzip(z2,{'D/a.jpg':b'MEDIA','D/a.jpg.json':_takeout_sidecar('a.jpg',dt,'new')});RunExecutor([z2],root).execute(run_id='r2')
    blob=next(x for x in (root/'metadata'/'assets').rglob('*') if 'blobs' in x.parts and x.is_file()).resolve();real=ga._checked_kind
    def fail(path,*,directory):
        if Path(path).resolve()==blob and not directory:return None,OSError(errno.EIO,'simulated blob entry stat failure')
        return real(path,directory=directory)
    monkeypatch.setattr(ga,'_checked_kind',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_blob_entry' for x in report.problems)


def test_archive_audit_reports_current_revision_read_eio_explicitly(tmp_path, monkeypatch):
    import errno, json, pathlib
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    result=RunExecutor([z],root).execute(run_id='r');aid=result['successes'][0]['asset_id'];ad=root/'metadata'/'assets'/aid
    rid=json.loads((ad/'current.json').read_text())['revision_id'];rp=(ad/'revisions'/f'{rid}.json').resolve();real=pathlib.Path.read_text;calls={'n':0}
    def fail(self,*args,**kwargs):
        if self.resolve()==rp:
            calls['n']+=1
            if calls['n']>=2:raise OSError(errno.EIO,'simulated current revision read failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'read_text',fail)
    report=ga.audit_archive(root)
    assert not report.ok and any(x.kind=='unreadable_current_revision' and 'read failure' in x.message for x in report.problems)


def test_archive_audit_classifies_current_pointer_read_eio_as_unreadable(tmp_path, monkeypatch):
    import errno, pathlib
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    result=RunExecutor([z],root).execute(run_id='r');aid=result['successes'][0]['asset_id'];target=(root/'metadata'/'assets'/aid/'current.json').resolve();real=pathlib.Path.read_text
    def fail(self,*args,**kwargs):
        if self.resolve()==target:raise OSError(errno.EIO,'simulated current pointer read failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'read_text',fail)
    report=ga.audit_archive(root)
    assert any(x.kind=='unreadable_current_pointer' for x in report.problems)
    assert not any(x.kind=='bad_current_pointer' for x in report.problems)


def test_archive_audit_classifies_revision_read_eio_as_unreadable(tmp_path, monkeypatch):
    import errno, json, pathlib
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    result=RunExecutor([z],root).execute(run_id='r');aid=result['successes'][0]['asset_id'];ad=root/'metadata'/'assets'/aid
    rid=json.loads((ad/'current.json').read_text())['revision_id'];target=(ad/'revisions'/f'{rid}.json').resolve();real=pathlib.Path.read_text
    def fail(self,*args,**kwargs):
        if self.resolve()==target:raise OSError(errno.EIO,'simulated immutable revision read failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'read_text',fail)
    report=ga.audit_archive(root)
    assert any(x.kind=='unreadable_revision' for x in report.problems)
    assert not any(x.kind=='bad_revision' for x in report.problems)


def test_archive_audit_classifies_xmp_xml_read_eio_as_unreadable_not_invalid(tmp_path, monkeypatch):
    import errno, pathlib
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA','D/a.jpg.json':_takeout_sidecar('a.jpg',datetime(2020,1,15,12),'caption')})
    result=RunExecutor([z],root).execute(run_id='r');target=(root/result['successes'][0]['xmp']).resolve();real=pathlib.Path.read_bytes
    def fail(self,*args,**kwargs):
        if self.resolve()==target:raise OSError(errno.EIO,'simulated xmp xml read failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'read_bytes',fail)
    report=ga.audit_archive(root)
    assert any(x.kind=='unreadable_xmp' and 'xml read failure' in x.message for x in report.problems)
    assert not any(x.kind=='invalid_xmp' for x in report.problems)


def _archive_with_identity_records(tmp_path):
    import json
    z=tmp_path/'identity.zip';root=tmp_path/'archive';raw=json.loads(_takeout_sidecar('a.jpg',datetime(2020,1,15,12),'caption'));raw['url']='https://photos.google.com/photo/ONE'
    mkzip(z,{'D/a.jpg':b'MEDIA','D/a.jpg.json':json.dumps(raw)})
    result=RunExecutor([z],root).execute(run_id='r');aid=result['successes'][0]['asset_id'];base=root/'metadata'/'assets'/aid/'identity'
    return root,next((base/'observations').glob('*.json')).resolve(),next((base/'sightings').glob('*.json')).resolve()


def test_archive_audit_classifies_identity_observation_read_eio_as_unreadable(tmp_path, monkeypatch):
    import errno, pathlib
    import gpa.audit as ga
    root,target,_=_archive_with_identity_records(tmp_path);real=pathlib.Path.read_text
    def fail(self,*args,**kwargs):
        if self.resolve()==target:raise OSError(errno.EIO,'simulated observation read failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'read_text',fail)
    report=ga.audit_archive(root)
    assert any(x.kind=='unreadable_identity_observation' for x in report.problems)
    assert not any(x.kind=='bad_identity_observation' for x in report.problems)


def test_archive_audit_classifies_identity_sighting_read_eio_as_unreadable(tmp_path, monkeypatch):
    import errno, pathlib
    import gpa.audit as ga
    root,_,target=_archive_with_identity_records(tmp_path);real=pathlib.Path.read_text
    def fail(self,*args,**kwargs):
        if self.resolve()==target:raise OSError(errno.EIO,'simulated sighting read failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'read_text',fail)
    report=ga.audit_archive(root)
    assert any(x.kind=='unreadable_identity_sighting' for x in report.problems)
    assert not any(x.kind=='bad_identity_sighting' for x in report.problems)


def test_archive_audit_classifies_run_manifest_read_eio_as_unreadable(tmp_path, monkeypatch):
    import errno, pathlib
    import gpa.audit as ga
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA','D/future.xyz':b'UNKNOWN'})
    RunExecutor([z],root).execute(run_id='r');target=next((root/'metadata'/'runs').glob('*.json')).resolve();real=pathlib.Path.read_text
    def fail(self,*args,**kwargs):
        if self.resolve()==target:raise OSError(errno.EIO,'simulated run manifest read failure')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'read_text',fail)
    report=ga.audit_archive(root)
    assert any(x.kind=='unreadable_run_manifest' for x in report.problems)
    assert not any(x.kind=='bad_run_manifest' for x in report.problems)


def test_archive_audit_reports_nonobject_run_manifest_without_crashing(tmp_path):
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    RunExecutor([z],root).execute(run_id='r');mp=next((root/'metadata'/'runs').glob('*.json'));mp.write_text('[]')
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='bad_run_manifest' and 'JSON object' in x.message for x in report.problems)


def test_archive_audit_reports_nonarray_unclassified_manifest_field_without_crashing(tmp_path):
    import json
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA'})
    RunExecutor([z],root).execute(run_id='r');mp=next((root/'metadata'/'runs').glob('*.json'));obj=json.loads(mp.read_text());obj['unclassified_preserved']='not-an-array';mp.write_text(json.dumps(obj))
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='bad_run_manifest' and 'JSON array' in x.message for x in report.problems)


def test_archive_audit_reports_scalar_unclassified_manifest_entry_and_continues(tmp_path):
    import json
    z=tmp_path/'z.zip';root=tmp_path/'archive';mkzip(z,{'D/a.jpg':b'MEDIA','D/future.xyz':b'UNKNOWN'})
    RunExecutor([z],root).execute(run_id='r');mp=next((root/'metadata'/'runs').glob('*.json'));obj=json.loads(mp.read_text());good=obj['unclassified_preserved'][0];obj['unclassified_preserved']=[42,good];mp.write_text(json.dumps(obj))
    report=audit_archive(root)
    assert not report.ok and any(x.kind=='bad_unclassified_manifest_entry' for x in report.problems)
    assert report.unclassified_checked==1
