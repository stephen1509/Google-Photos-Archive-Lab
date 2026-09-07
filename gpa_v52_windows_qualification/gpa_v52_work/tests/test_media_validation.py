from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gpa.media_validation import validate_media
from gpa.audit import audit_archive
from gpa.verification import verify_migration


def _jpeg(path: Path):
    from PIL import Image
    Image.new('RGB',(4,3),(10,20,30)).save(path,'JPEG')


def test_valid_jpeg_records_pillow_version(tmp_path):
    p=tmp_path/'a.jpg';_jpeg(p)
    r=validate_media(p)
    assert r.status=='passed' and r.validator=='Pillow'
    assert r.version and r.version.lower()!='unknown'
    assert r.validator_sha256 and len(r.validator_sha256)==64
    assert r.media_sha256 and len(r.media_sha256)==64 and r.bytes_unchanged is True


def test_corrupt_jpeg_is_failed_not_unavailable(tmp_path):
    p=tmp_path/'bad.jpg';p.write_bytes(b'not-a-jpeg')
    r=validate_media(p)
    assert r.status=='failed' and r.validator=='Pillow' and r.version


def test_invalid_raw_fails_closed_without_claiming_validation_passed(tmp_path):
    p=tmp_path/'a.cr3';p.write_bytes(b'raw')
    r=validate_media(p)
    assert r.status in {'unavailable','failed'} and r.status!='passed'
    assert 'RAW' in (r.validator or '')


def test_valid_mp4_requires_video_stream_and_records_ffprobe_version(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('ffmpeg/ffprobe unavailable')
    p=tmp_path/'a.mp4'
    subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','color=size=16x16:rate=1:color=black','-t','1','-c:v','mpeg4','-y',str(p)],check=True)
    r=validate_media(p)
    assert r.status=='passed' and r.validator=='ffprobe' and r.version and (r.video_streams or 0)>=1
    assert r.validator_sha256 and len(r.validator_sha256)==64
    assert r.media_sha256 and r.bytes_unchanged is True


def test_audio_only_mp4_fails_video_validation(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('ffmpeg/ffprobe unavailable')
    p=tmp_path/'audio.mp4'
    subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=0.2','-c:a','aac','-y',str(p)],check=True)
    r=validate_media(p)
    assert r.status=='failed' and r.video_streams==0 and 'no video stream' in r.detail


def test_heif_validator_records_libheif_version_when_available(tmp_path):
    if not shutil.which('heif-convert'):pytest.skip('heif-convert unavailable')
    # A fake HEIC proves the validator is present and produces a failed, versioned result.
    p=tmp_path/'bad.heic';p.write_bytes(b'not-heic')
    r=validate_media(p)
    assert r.status=='failed' and r.validator=='libheif/heif-convert + Pillow' and r.version and r.version.lower()!='unknown'
    assert r.validator_sha256 and len(r.validator_sha256)==64 and r.media_sha256 and r.bytes_unchanged is True


def test_explicit_heif_converter_path_is_preferred_and_invalid_override_fails_closed(tmp_path, monkeypatch):
    from gpa.media_validation import _heif_convert_executable
    chosen = tmp_path / 'qualified-heif-convert.exe'
    chosen.write_bytes(b'qualification fixture')
    monkeypatch.setenv('GPA_HEIF_CONVERT', str(chosen))
    assert _heif_convert_executable() == (str(chosen), None)
    missing = tmp_path / 'missing-heif-convert.exe'
    monkeypatch.setenv('GPA_HEIF_CONVERT', str(missing))
    resolved, error = _heif_convert_executable()
    assert resolved is None and error and 'does not name a regular file' in error


def _minimal_asset(root:Path, *, media_validation:dict):
    import hashlib
    media=root/'Photos'/'2017'/'05 - May'/'a.jpg';media.parent.mkdir(parents=True);_jpeg(media)
    sha=hashlib.sha256(media.read_bytes()).hexdigest();aid=sha[:24]
    if media_validation.get('status')=='passed':
        media_validation=dict(media_validation)
        media_validation.setdefault('validator_sha256','a'*64)
        media_validation.setdefault('media_sha256',sha)
        media_validation.setdefault('bytes_unchanged',True)
        media_validation.setdefault('schema','gpa.media-validation.v1')
    rec={'schema':'gpa.asset.v1','asset_id':aid,'media_sha256':sha,'projection':{'media':str(media.relative_to(root)),'xmp':None,'xmp_sha256':None},'date':None,'placement_date':None,'media_validation':media_validation,'warnings':[]}
    canonical=json.dumps(rec,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();rid=hashlib.sha256(canonical).hexdigest()[:16]
    rec['revision_id']=rid
    ad=root/'metadata'/'assets'/aid;(ad/'revisions').mkdir(parents=True)
    (ad/'revisions'/f'{rid}.json').write_text(json.dumps(rec),encoding='utf-8')
    (ad/'current.json').write_text(json.dumps({'revision_id':rid}),encoding='utf-8')
    return aid


def test_audit_rejects_passed_validation_without_version_provenance(tmp_path):
    root=tmp_path/'archive';_minimal_asset(root,media_validation={'status':'passed','validator':'Pillow','version':None,'detail':''})
    a=audit_archive(root)
    assert not a.ok and any(p.kind=='bad_media_validation_provenance' for p in a.problems)


def test_failed_media_validation_is_preservation_evidence_not_hash_corruption(tmp_path):
    root=tmp_path/'archive';_minimal_asset(root,media_validation={'status':'failed','validator':'Pillow','version':'12.3.0','detail':'decode failed'})
    a=audit_archive(root)
    assert a.ok and a.media_validation_failed==1 and a.media_validation_passed==0


def test_retirement_gate_blocks_unvalidated_current_media(tmp_path):
    root=tmp_path/'archive';_minimal_asset(root,media_validation={'status':'unavailable','validator':'RAW validator','version':None,'detail':'not configured'})
    (root/'metadata'/'runs').mkdir(parents=True)
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':[],'source_problems':[],'assets_planned':1,'assets_committed':1,'failures':[],'unclassified_preserved':[]}),encoding='utf-8')
    v=verify_migration(root,all_takeout_parts_confirmed=True,redundant_copy_verified=True,require_source_vault=False,require_takeout_receipt=False,require_exit_evidence=False,require_independent_media=False)
    assert not v.media_validation_complete and not v.google_retirement_ready
    assert any('qualified read-only media/container validator' in b for b in v.blockers)


def test_retirement_gate_can_explicitly_ignore_validation_for_legacy_analysis(tmp_path):
    root=tmp_path/'archive';_minimal_asset(root,media_validation={'status':'unavailable','validator':'RAW validator','version':None,'detail':'not configured'})
    (root/'metadata'/'runs').mkdir(parents=True)
    # Keep source fingerprint empty so archive_integrity won't be true; this test is only about the validation blocker toggle.
    (root/'metadata'/'runs'/'r.json').write_text(json.dumps({'status':'complete','source_archives':[],'source_problems':[],'assets_planned':1,'assets_committed':1,'failures':[],'unclassified_preserved':[]}),encoding='utf-8')
    v=verify_migration(root,all_takeout_parts_confirmed=True,redundant_copy_verified=True,require_source_vault=False,require_takeout_receipt=False,require_exit_evidence=False,require_independent_media=False,require_media_validation=False)
    assert not any('qualified read-only media/container validator' in b for b in v.blockers)


def test_validation_is_bound_to_exact_media_hash(tmp_path):
    import hashlib
    p=tmp_path/'bound.jpg';_jpeg(p)
    expected=hashlib.sha256(p.read_bytes()).hexdigest()
    r=validate_media(p,expected_sha256=expected)
    assert r.status=='passed' and r.media_sha256==expected and r.bytes_unchanged is True


def test_wrong_expected_hash_fails_even_when_image_decodes(tmp_path):
    p=tmp_path/'wrong.jpg';_jpeg(p)
    r=validate_media(p,expected_sha256='0'*64)
    assert r.status=='failed' and r.bytes_unchanged is False
    assert 'media bytes changed during read-only validation' in r.detail


def test_audit_rejects_passed_validation_bound_to_other_media(tmp_path):
    root=tmp_path/'archive'
    _minimal_asset(root,media_validation={'status':'passed','validator':'Pillow','version':'12.3.0','validator_sha256':'a'*64,'media_sha256':'b'*64,'bytes_unchanged':True,'detail':''})
    # Helper intentionally preserves the provided wrong media_sha256.
    a=audit_archive(root)
    assert not a.ok and any(p.kind=='bad_media_validation_provenance' for p in a.problems)


def test_audit_rejects_passed_validation_without_exact_build_fingerprint(tmp_path):
    root=tmp_path/'archive'
    aid=_minimal_asset(root,media_validation={'status':'passed','validator':'Pillow','version':'12.3.0','validator_sha256':'','detail':''})
    a=audit_archive(root)
    assert not a.ok and any(p.kind=='bad_media_validation_provenance' for p in a.problems)


def test_incremental_relocation_preserves_validation_bound_to_same_media(tmp_path):
    import hashlib, json, zipfile
    from datetime import datetime, timezone
    from gpa.planner import LibraryPlanner
    from gpa.materializer import ArchiveMaterializer
    from gpa.incremental import IncrementalUpdater

    def mkzip(path, ts):
        from PIL import Image
        import io
        b=io.BytesIO();Image.new('RGB',(4,3),(20,30,40)).save(b,'JPEG');raw=b.getvalue()
        side=json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':str(ts)}})
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('D/a.jpg',raw);z.writestr('D/a.jpg.json',side)
        return raw

    z1=tmp_path/'one.zip';raw=mkzip(z1,int(datetime(2020,1,15,12,tzinfo=timezone.utc).timestamp()))
    p1=LibraryPlanner([z1]);a1=p1.plan()[0];root=tmp_path/'archive';m1=ArchiveMaterializer(root,p1.lib);first=m1.materialize(a1)
    rec1=json.loads(first['revision'].read_text());mv1=rec1['media_validation']
    assert mv1['status']=='passed' and mv1['media_sha256']==hashlib.sha256(raw).hexdigest()

    # Same exact media bytes, corrected Google date => different month/path.
    z2=tmp_path/'two.zip';mkzip(z2,int(datetime(2019,12,15,12,tzinfo=timezone.utc).timestamp()))
    p2=LibraryPlanner([z2]);a2=p2.plan()[0];m2=ArchiveMaterializer(root,p2.lib)
    out=IncrementalUpdater(root,m2).apply(a2)
    rec2=json.loads(out.revision.read_text())
    assert out.action=='relocated'
    assert rec2['media_validation']==mv1
    assert rec2['media_validation']['media_sha256']==rec2['media_sha256']


def test_incremental_relocation_does_not_promote_unbound_legacy_validation(tmp_path):
    import hashlib, json, zipfile
    from datetime import datetime, timezone
    from gpa.planner import LibraryPlanner
    from gpa.materializer import ArchiveMaterializer
    from gpa.incremental import IncrementalUpdater

    from PIL import Image
    import io
    b=io.BytesIO();Image.new('RGB',(4,3),(1,2,3)).save(b,'JPEG');raw=b.getvalue()
    def make(path, year, month):
        ts=int(datetime(year,month,15,12,tzinfo=timezone.utc).timestamp())
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('D/a.jpg',raw);z.writestr('D/a.jpg.json',json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':str(ts)}}))
    z1=tmp_path/'one.zip';make(z1,2020,1);p1=LibraryPlanner([z1]);a1=p1.plan()[0];root=tmp_path/'archive';m1=ArchiveMaterializer(root,p1.lib);first=m1.materialize(a1)
    # Mutate only the test's immutable revision to simulate a legacy unbound validation record,
    # then recompute its content-addressed id/current pointer so the updater can read it normally.
    rec=json.loads(first['revision'].read_text());rec['media_validation']={'status':'passed','validator':'old','version':'1'}
    rec.pop('revision_id',None)
    from gpa.revisions import RevisionStore
    store=RevisionStore(root);newrev=store.add_revision(a1.asset_id,rec)
    z2=tmp_path/'two.zip';make(z2,2019,12);p2=LibraryPlanner([z2]);a2=p2.plan()[0];m2=ArchiveMaterializer(root,p2.lib)
    plan=IncrementalUpdater(root,m2).inspect(a2)
    assert plan.action=='relocated'
    assert plan.record.get('media_validation') is None


def test_reproducible_real_jpeg_incremental_stress_tool_smoke(tmp_path):
    import json, os, subprocess, sys
    from pathlib import Path
    tool=Path(__file__).resolve().parents[1]/'tools'/'stress_jpeg_incremental.py'
    report=tmp_path/'report.json';work=tmp_path/'stress'
    cp=subprocess.run([sys.executable,str(tool),'--workdir',str(work),'--count','12','--report',str(report)],capture_output=True,text=True,timeout=60)
    assert cp.returncode==0, cp.stdout+cp.stderr
    obj=json.loads(report.read_text())
    assert obj['passed'] and obj['actions']['initial']=={'new':12}
    assert obj['actions']['reimport']=={'unchanged':12} and obj['actions']['corrected']=={'relocated':12}
    assert obj['audit']['assets']==12 and obj['audit']['revisions']==24 and obj['audit']['validation_passed']==12


def test_reproducible_mixed_jpeg_mp4_incremental_stress_tool_smoke(tmp_path):
    import json, shutil, subprocess, sys
    from pathlib import Path
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        import pytest
        pytest.skip('ffmpeg/ffprobe unavailable')
    tool=Path(__file__).resolve().parents[1]/'tools'/'stress_mixed_incremental.py'
    report=tmp_path/'mixed-report.json';work=tmp_path/'mixed-stress'
    cp=subprocess.run([sys.executable,str(tool),'--workdir',str(work),'--photos','4','--videos','2','--report',str(report)],capture_output=True,text=True,timeout=90)
    assert cp.returncode==0, cp.stdout+cp.stderr
    obj=json.loads(report.read_text())
    assert obj['passed'] and obj['actions']['initial']=={'new':6}
    assert obj['actions']['reimport']=={'unchanged':6} and obj['actions']['corrected']=={'relocated':6}
    assert obj['audit']['assets']==6 and obj['audit']['revisions']==12 and obj['audit']['validation_passed']==6
    assert obj['validation_counts']['passed:Pillow']==4
    assert obj['validation_counts']['passed:ffprobe']==2


def test_ffprobe_embedded_raw_evidence_omits_ephemeral_probe_filename():
    from gpa.embedded import embedded_from_ffprobe
    probe={'format':{'filename':'/tmp/gpa-probe-batch-random/a.mp4','duration':'1.0','tags':{'comment':'keep me'}},'streams':[{'codec_type':'video','tags':{}}]}
    e=embedded_from_ffprobe(probe)
    assert 'filename' not in e.raw['ffprobe']['format']
    assert e.raw['ffprobe']['format']['duration']=='1.0'
    assert e.raw['ffprobe']['format']['tags']['comment']=='keep me'
    # Caller-owned probe input is not mutated by evidence normalization.
    assert probe['format']['filename'].startswith('/tmp/gpa-probe-batch-')


def test_reproducible_mixed_media_fault_recovery_stress_tool_smoke(tmp_path):
    import json, shutil, subprocess, sys
    from pathlib import Path
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        import pytest
        pytest.skip('ffmpeg/ffprobe unavailable')
    tool=Path(__file__).resolve().parents[1]/'tools'/'stress_fault_recovery.py'
    report=tmp_path/'fault-report.json';work=tmp_path/'fault-stress'
    cp=subprocess.run([sys.executable,str(tool),'--workdir',str(work),'--photos','4','--videos','2','--report',str(report)],capture_output=True,text=True,timeout=90)
    assert cp.returncode==0, cp.stdout+cp.stderr
    obj=json.loads(report.read_text())
    assert obj['passed']
    assert obj['resume_actions']=={'new':4,'unchanged':2}
    assert obj['healed_actions']=={'relocated':6}
    assert obj['audit']['assets']==6 and obj['audit']['revisions']==12 and obj['audit']['validation_passed']==6
    assert obj['checks']['photo_projection_crash_reproduced'] and obj['checks']['video_projection_crash_reproduced']
