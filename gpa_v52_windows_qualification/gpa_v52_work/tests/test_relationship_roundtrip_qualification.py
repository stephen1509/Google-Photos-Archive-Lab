from __future__ import annotations

import json
from pathlib import Path

import pytest

from gpa.relationship_roundtrip_qualification import (
    RELATIONSHIP_QUALIFICATION_SCHEMA,
    MOTION_PHOTO_HARNESS_ID,
    MOTION_PHOTO_PROFILE_ID,
    JPEG_PROFILE_ID,
    MOV_PROFILE_ID,
    LIVE_PHOTO_HARNESS_ID,
    LIVE_PHOTO_PROFILE_ID,
    HEIC_PROFILE_ID,
    AVIF_PROFILE_ID,
    expected_relationship_qualification_id,
    qualify_exiftool_motion_photo,
    qualify_exiftool_live_photo,
    write_relationship_qualification_report,
)


def _fake(path: Path, *, mode: str = 'good', version: str = '13.55') -> Path:
    path.mkdir(parents=True, exist_ok=True)
    p = path / 'exiftool-motion-fake.py'
    p.write_text(f'''#!/usr/bin/env python3
import json,sys
from pathlib import Path
MODE={mode!r}; VERSION={version!r}
if MODE=='hang':
    import time;time.sleep(5)
if '-ver' in sys.argv:
    print(VERSION);raise SystemExit(0)
file=Path(sys.argv[-1]);meta=Path(str(file)+'.fake-meta.json')
if '-j' in sys.argv:
    d=json.loads(meta.read_text()) if meta.exists() else {{}}
    if MODE=='wrong-readback': d['XMP-dc:Description']='wrong'
    print(json.dumps([d]));raise SystemExit(0)
vals={{}}
for a in sys.argv[1:-1]:
    if a.startswith('-') and '=' in a:
        k,v=a[1:].split('=',1)
        if k=='XMP-photoshop:DateCreated': vals['XMP-photoshop:DateCreated']=v
        elif k in ('dc:description','XMP-dc:Description'): vals['XMP-dc:Description']=v
b=bytearray(file.read_bytes())
if MODE!='no-change':
    payload=b'GPA relationship qualification'
    if file.suffix.lower() in ('.mov','.mp4','.m4v'):
        box=(8+len(payload)).to_bytes(4,'big')+b'free'+payload
        b.extend(box)
    elif file.suffix.lower() in ('.heic','.avif'):
        box=(8+len(payload)).to_bytes(4,'big')+b'free'+payload
        i=bytes(b).rfind(b'mpvd')
        if i<4: raise SystemExit('missing mpvd')
        b=bytearray(bytes(b[:i-4])+box+bytes(b[i-4:]))
    else:
        seg=b'\\xff\\xfe'+(len(payload)+2).to_bytes(2,'big')+payload
        b=bytearray(bytes(b[:2])+seg+bytes(b[2:]))
if MODE=='video-change' and b:
    if file.suffix.lower() in ('.mov','.mp4','.m4v'):
        i=bytes(b).find(b'mdat')
        if i>=0 and i+8<len(b): b[i+8]^=1
        else: b[-1]^=1
    else:
        b[-1]^=1
elif MODE=='still-payload-change' and file.suffix.lower() in ('.heic','.avif'):
    i=bytes(b).find(b'mdat')
    if i>=0 and i+8<len(b): b[i+8]^=1
elif MODE=='duplicate-xmp' and file.suffix.lower() in ('.jpg','.jpeg'):
    h=b'http://ns.adobe.com/xap/1.0/\\x00';i=bytes(b).find(h)
    if i>=4:
        st=i-4;L=int.from_bytes(b[st+2:st+4],'big');seg=bytes(b[st:st+2+L])
        b=bytearray(bytes(b[:2])+seg+bytes(b[2:]))
elif MODE=='structure-break':
    old=b'MotionPhoto="1"';new=b'MotionPhoto="0"'
    i=bytes(b).find(old)
    if i>=0:b[i:i+len(old)]=new
elif MODE=='strip-video' and len(b)>128:
    del b[-128:]
elif MODE=='content-id-break' and file.suffix.lower()=='.mov':
    import re
    matches=list(re.finditer(rb'[0-9A-F]{{8}}-[0-9A-F]{{4}}-[0-9A-F]{{4}}-[0-9A-F]{{4}}-[0-9A-F]{{12}}',bytes(b)))
    for m in matches:
        b[m.start()]=ord('0') if b[m.start()]!=ord('0') else ord('1')
elif MODE=='timing-break' and file.suffix.lower()=='.mov':
    old=b'com.apple.quicktime.still-image-time';new=b'com.apple.quicktime.still-image-timX'
    i=bytes(b).find(old)
    if i>=0:b[i:i+len(old)]=new
file.write_bytes(bytes(b))
meta.write_text(json.dumps(vals))
print('1 files updated')
''')
    p.chmod(0o755)
    return p


def test_good_motion_photo_candidate_preserves_composite_relationship(tmp_path):
    r = qualify_exiftool_motion_photo(_fake(tmp_path), tmp_path/'work')
    assert r.schema == RELATIONSHIP_QUALIFICATION_SCHEMA
    assert r.harness == MOTION_PHOTO_HARNESS_ID
    assert r.relationship_profile_id == MOTION_PHOTO_PROFILE_ID
    assert r.format_profile_id == JPEG_PROFILE_ID
    assert r.passed
    assert r.checks['relationship_structure_preserved']
    assert r.checks['still_payload_unchanged']
    assert r.checks['video_bytes_unchanged'] and r.checks['video_mdat_unchanged']
    assert r.checks['video_terminal_after_write'] and r.checks['motion_v1_not_legacy_after_write']
    assert r.checks['still_decodable_after_write'] and r.checks['video_decodable_after_write']
    assert r.checks['readback_description'] and r.checks['readback_xmp_date']
    assert r.production_write_approved is False
    assert r.qualification_id == expected_relationship_qualification_id(r.to_dict())


@pytest.mark.parametrize('mode,failed_check', [
    ('video-change', 'video_bytes_unchanged'),
    ('structure-break', 'relationship_structure_preserved'),
    ('strip-video', 'relationship_structure_preserved'),
])
def test_motion_photo_relationship_damage_fails_closed(tmp_path, mode, failed_check):
    r = qualify_exiftool_motion_photo(_fake(tmp_path, mode=mode), tmp_path/'work')
    assert not r.passed
    assert r.checks.get(failed_check) is False


def test_duplicate_structural_motion_xmp_fails_closed_as_ambiguous(tmp_path):
    r = qualify_exiftool_motion_photo(_fake(tmp_path, mode='duplicate-xmp'), tmp_path/'work')
    assert not r.passed
    assert r.checks.get('relationship_structure_preserved') is False
    assert any('ambiguous' in e.lower() for e in r.errors)


def test_noop_wrong_readback_and_hung_candidate_fail(tmp_path):
    noop = qualify_exiftool_motion_photo(_fake(tmp_path/'a', mode='no-change'), tmp_path/'w1')
    wrong = qualify_exiftool_motion_photo(_fake(tmp_path/'b', mode='wrong-readback'), tmp_path/'w2')
    hung = qualify_exiftool_motion_photo(_fake(tmp_path/'c', mode='hang'), tmp_path/'w3', subprocess_timeout_seconds=0.1)
    assert not noop.passed and not noop.checks['whole_file_changed']
    assert not wrong.passed and not wrong.checks['readback_description']
    assert not hung.passed and any('timed out' in e.lower() for e in hung.errors)


def test_relationship_qualification_binds_distribution_identity(tmp_path):
    dist = tmp_path/'dist'; dist.mkdir()
    exe = _fake(dist); (dist/'exiftool_files').mkdir(); (dist/'exiftool_files/x.pm').write_text('x')
    r = qualify_exiftool_motion_photo(exe, tmp_path/'work', distribution_root=dist)
    assert r.passed and r.checks['distribution_fingerprinted'] and len(r.distribution_sha256 or '') == 64


def test_relationship_report_is_write_once_and_timeout_validation(tmp_path):
    r = qualify_exiftool_motion_photo(_fake(tmp_path), tmp_path/'work')
    out = tmp_path/'relationship.json'
    write_relationship_qualification_report(out, r)
    obj = json.loads(out.read_text())
    assert obj['qualification_id'] == r.qualification_id and obj['passed'] is True
    with pytest.raises(FileExistsError):
        write_relationship_qualification_report(out, r)
    with pytest.raises(ValueError):
        qualify_exiftool_motion_photo(_fake(tmp_path/'z'), tmp_path/'zero', subprocess_timeout_seconds=0)


def test_cli_motion_photo_relationship_qualification_writes_report(tmp_path, capsys):
    from gpa.__main__ import main
    exe=_fake(tmp_path);report=tmp_path/'relationship.json'
    rc=main(['qualify-exiftool-relationship','--executable',str(exe),'--workdir',str(tmp_path/'work'),
             '--relationship-profile','motion-photo-composite-v1','--report',str(report)])
    obj=json.loads(capsys.readouterr().out)
    assert rc==0 and obj['passed'] and obj['harness']==MOTION_PHOTO_HARNESS_ID and report.exists()


def test_good_live_photo_candidate_preserves_pair_relationship(tmp_path):
    r = qualify_exiftool_live_photo(_fake(tmp_path), tmp_path/'work')
    assert r.schema == RELATIONSHIP_QUALIFICATION_SCHEMA
    assert r.harness == LIVE_PHOTO_HARNESS_ID
    assert r.relationship_profile_id == LIVE_PHOTO_PROFILE_ID
    assert r.format_profile_id == JPEG_PROFILE_ID
    assert r.secondary_format_profile_id == MOV_PROFILE_ID
    assert r.passed
    assert r.checks['content_identifier_preserved']
    assert r.checks['timing_resource_preserved']
    assert r.checks['still_payload_unchanged'] and r.checks['video_mdat_unchanged']
    assert r.checks['still_decodable_after_write'] and r.checks['video_decodable_after_write']
    assert r.checks['readback_description'] and r.checks['readback_xmp_date']
    assert r.fixture_generator['apple_photos_acceptance_claimed'] is False
    assert r.production_write_approved is False
    assert r.qualification_id == expected_relationship_qualification_id(r.to_dict())


@pytest.mark.parametrize('mode,failed_check', [
    ('content-id-break', 'content_identifier_preserved'),
    ('timing-break', 'timing_resource_preserved'),
    ('video-change', 'video_mdat_unchanged'),
])
def test_live_photo_relationship_damage_fails_closed(tmp_path, mode, failed_check):
    r = qualify_exiftool_live_photo(_fake(tmp_path, mode=mode), tmp_path/'work')
    assert not r.passed
    assert r.checks.get(failed_check) is False


def test_live_photo_relationship_binds_distribution_identity(tmp_path):
    dist = tmp_path/'dist'; dist.mkdir()
    exe = _fake(dist); (dist/'exiftool_files').mkdir(); (dist/'exiftool_files/x.pm').write_text('x')
    r = qualify_exiftool_live_photo(exe, tmp_path/'work', distribution_root=dist)
    assert r.passed and r.checks['distribution_fingerprinted'] and len(r.distribution_sha256 or '') == 64


def test_cli_live_photo_relationship_qualification_writes_report(tmp_path, capsys):
    from gpa.__main__ import main
    exe=_fake(tmp_path);report=tmp_path/'relationship-live.json'
    rc=main(['qualify-exiftool-relationship','--executable',str(exe),'--workdir',str(tmp_path/'work'),
             '--relationship-profile','live-photo-pair-v1','--report',str(report)])
    obj=json.loads(capsys.readouterr().out)
    assert rc==0 and obj['passed'] and obj['harness']==LIVE_PHOTO_HARNESS_ID and report.exists()
    assert obj['secondary_format_profile_id']==MOV_PROFILE_ID


def test_live_photo_noop_wrong_readback_and_hung_candidate_fail(tmp_path):
    noop = qualify_exiftool_live_photo(_fake(tmp_path/'la', mode='no-change'), tmp_path/'lw1')
    wrong = qualify_exiftool_live_photo(_fake(tmp_path/'lb', mode='wrong-readback'), tmp_path/'lw2')
    hung = qualify_exiftool_live_photo(_fake(tmp_path/'lc', mode='hang'), tmp_path/'lw3', subprocess_timeout_seconds=0.1)
    assert not noop.passed and not noop.checks['still_file_changed'] and not noop.checks['movie_file_changed']
    assert not wrong.passed and not wrong.checks['readback_description']
    assert not hung.passed and any('timed out' in e.lower() for e in hung.errors)


HEIC_TEST_FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'heic' / 'rainbow-451x461.heic'


def test_good_avif_motion_photo_candidate_preserves_isobmff_relationship(tmp_path):
    r = qualify_exiftool_motion_photo(
        _fake(tmp_path), tmp_path/'work', format_profile_id=AVIF_PROFILE_ID
    )
    assert r.passed
    assert r.harness == MOTION_PHOTO_HARNESS_ID
    assert r.format_profile_id == AVIF_PROFILE_ID
    assert r.checks['fixture_mpvd_header_exact'] and r.checks['mpvd_header_preserved']
    assert r.checks['still_payload_unchanged']
    assert r.checks['video_bytes_unchanged'] and r.checks['video_mdat_unchanged']
    assert r.checks['still_decodable_after_write'] and r.checks['video_decodable_after_write']
    assert r.qualification_id == expected_relationship_qualification_id(r.to_dict())


def test_good_heic_motion_photo_candidate_uses_exact_pinned_fixture_and_preserves_relationship(tmp_path):
    source_before = HEIC_TEST_FIXTURE.read_bytes()
    r = qualify_exiftool_motion_photo(
        _fake(tmp_path), tmp_path/'work', format_profile_id=HEIC_PROFILE_ID,
        qualification_fixture=HEIC_TEST_FIXTURE,
    )
    assert r.passed
    assert r.format_profile_id == HEIC_PROFILE_ID
    assert r.checks['fixture_source_admitted'] and r.checks['fixture_pinned_primary_payload_preserved']
    assert r.fixture_generator['fixture_admission']['fixture_id'] == 'libheif-rainbow-451x461-heic-v1'
    assert r.checks['fixture_mpvd_header_exact'] and r.checks['mpvd_header_preserved']
    assert r.checks['still_payload_unchanged']
    assert HEIC_TEST_FIXTURE.read_bytes() == source_before


@pytest.mark.parametrize('profile,fixture', [
    (AVIF_PROFILE_ID, None),
    (HEIC_PROFILE_ID, HEIC_TEST_FIXTURE),
])
def test_isobmff_motion_photo_relationship_damage_fails_closed(tmp_path, profile, fixture):
    r = qualify_exiftool_motion_photo(
        _fake(tmp_path, mode='still-payload-change'), tmp_path/'work',
        format_profile_id=profile, qualification_fixture=fixture,
    )
    assert not r.passed
    assert r.checks.get('still_payload_unchanged') is False


def test_heic_motion_photo_requires_exact_pinned_fixture(tmp_path):
    exe = _fake(tmp_path/'fake')
    missing = qualify_exiftool_motion_photo(
        exe, tmp_path/'w0', format_profile_id=HEIC_PROFILE_ID
    )
    assert not missing.passed and any('requires --fixture' in e for e in missing.errors)
    bad = tmp_path/'bad.heic'; bad.write_bytes(HEIC_TEST_FIXTURE.read_bytes()[:-1])
    wrong = qualify_exiftool_motion_photo(
        exe, tmp_path/'w1', format_profile_id=HEIC_PROFILE_ID, qualification_fixture=bad
    )
    assert not wrong.passed and any('fixture' in e.lower() for e in wrong.errors)


def test_cli_avif_motion_photo_relationship_qualification_writes_report(tmp_path, capsys):
    from gpa.__main__ import main
    exe=_fake(tmp_path);report=tmp_path/'relationship-avif.json'
    rc=main(['qualify-exiftool-relationship','--executable',str(exe),'--workdir',str(tmp_path/'work'),
             '--relationship-profile','motion-photo-composite-v1','--format-profile','avif-single-v1',
             '--report',str(report)])
    obj=json.loads(capsys.readouterr().out)
    assert rc==0 and obj['passed'] and obj['format_profile_id']==AVIF_PROFILE_ID and report.exists()


def test_cli_heic_motion_photo_relationship_qualification_requires_pinned_fixture(tmp_path, capsys):
    from gpa.__main__ import main
    exe=_fake(tmp_path);report=tmp_path/'relationship-heic.json'
    rc=main(['qualify-exiftool-relationship','--executable',str(exe),'--workdir',str(tmp_path/'work'),
             '--relationship-profile','motion-photo-composite-v1','--format-profile','heic-single-v1',
             '--fixture',str(HEIC_TEST_FIXTURE),'--report',str(report)])
    obj=json.loads(capsys.readouterr().out)
    assert rc==0 and obj['passed'] and obj['format_profile_id']==HEIC_PROFILE_ID
    assert obj['checks']['fixture_source_admitted'] is True and report.exists()
