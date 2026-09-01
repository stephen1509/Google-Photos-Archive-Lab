from __future__ import annotations

import json
from pathlib import Path
import zlib

import pytest

from gpa.format_roundtrip_qualification import (
    FORMAT_QUALIFICATION_SCHEMA,
    JPEG_HARNESS_ID,
    PNG_HARNESS_ID,
    QUICKTIME_HARNESS_ID,
    AVIF_HARNESS_ID,
    qualify_exiftool_format,
    write_format_qualification_report,
)


def _fake(path: Path, *, mode='good', version='13.55') -> Path:
    path.mkdir(parents=True, exist_ok=True)
    p = path / 'exiftool-format-fake.py'
    p.write_text(f'''#!/usr/bin/env python3
import json,sys,zlib
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
if MODE!='no-change':
    if file.suffix.lower() in ('.jpg','.jpeg'):
        if MODE=='payload-change':
            from PIL import Image
            im=Image.open(file).convert('RGB');px=im.load();px[0,0]=(255,0,0);im.save(file,'JPEG',quality=70)
        else:
            b=file.read_bytes();payload=b'GPA-FORMAT-QUALIFIED';seg=b'\\xff\\xfe'+(len(payload)+2).to_bytes(2,'big')+payload
            file.write_bytes(b[:2]+seg+b[2:])
    elif file.suffix.lower()=='.png':
        if MODE=='payload-change':
            from PIL import Image
            im=Image.open(file).convert('RGBA');px=im.load();px[0,0]=(1,2,3,255);im.save(file,'PNG',compress_level=9)
        else:
            b=file.read_bytes();pos=b.rfind(b'IEND')-4
            payload=b'Comment\\x00GPA';typ=b'tEXt';chunk=len(payload).to_bytes(4,'big')+typ+payload+zlib.crc32(typ+payload).to_bytes(4,'big')
            file.write_bytes(b[:pos]+chunk+b[pos:])
    else:
        b=bytearray(file.read_bytes())
        if MODE=='payload-change':
            i=0
            while i+8<=len(b):
                size=int.from_bytes(b[i:i+4],'big');typ=bytes(b[i+4:i+8]);header=8
                if size==1: size=int.from_bytes(b[i+8:i+16],'big');header=16
                elif size==0: size=len(b)-i
                if typ==b'mdat' and size>header:
                    b[i+header]^=1;break
                i+=size
            file.write_bytes(bytes(b))
        else:
            payload=b'GPA-QUAL';box=(8+len(payload)).to_bytes(4,'big')+b'free'+payload
            file.write_bytes(bytes(b)+box)
meta.write_text(json.dumps(vals))
print('1 files updated')
''')
    p.chmod(0o755)
    return p



def test_good_jpeg_candidate_passes_format_payload_decode_and_readback(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path), tmp_path/'work', format_profile_id='jpeg-single-v1')
    assert r.schema == FORMAT_QUALIFICATION_SCHEMA and r.harness == JPEG_HARNESS_ID and r.passed
    assert Path(r.fixture_path).suffix == '.jpg'
    assert r.checks['whole_file_changed'] and r.checks['media_payload_unchanged'] and r.checks['output_decodable']
    assert r.checks['validator_identified'] and r.checks['validator_build_fingerprinted']
    assert r.checks['readback_description'] and r.checks['readback_xmp_date']


def test_jpeg_payload_change_fails_format_qualification(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path, mode='payload-change'), tmp_path/'work', format_profile_id='jpeg-single-v1')
    assert not r.passed and not r.checks['media_payload_unchanged']


def test_good_png_candidate_passes_payload_decode_and_readback(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path), tmp_path/'work', format_profile_id='png-single-v1')
    assert r.schema == FORMAT_QUALIFICATION_SCHEMA and r.harness == PNG_HARNESS_ID and r.passed
    assert r.checks['whole_file_changed'] and r.checks['media_payload_unchanged'] and r.checks['output_decodable']
    assert r.checks['readback_description'] and r.checks['readback_xmp_date']


def test_png_pixel_payload_change_fails_even_when_output_decodes(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path, mode='payload-change'), tmp_path/'work', format_profile_id='png-single-v1')
    assert not r.passed and not r.checks['media_payload_unchanged']


def test_png_noop_and_wrong_readback_fail(tmp_path):
    noop = qualify_exiftool_format(_fake(tmp_path/'a', mode='no-change'), tmp_path/'w1', format_profile_id='png-single-v1')
    wrong = qualify_exiftool_format(_fake(tmp_path/'b', mode='wrong-readback'), tmp_path/'w2', format_profile_id='png-single-v1')
    assert not noop.passed and not noop.checks['whole_file_changed']
    assert not wrong.passed and not wrong.checks['readback_description']


def test_good_quicktime_candidate_passes_mdat_decode_and_readback(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path), tmp_path/'work', format_profile_id='mp4-single-v1')
    assert r.harness == QUICKTIME_HARNESS_ID and r.passed
    assert r.checks['fixture_valid_before'] and r.checks['media_payload_unchanged'] and r.checks['output_decodable']
    assert r.validator['validator'] == 'ffprobe' and len(r.validator['validator_sha256']) == 64
    assert len(r.fixture_generator['sha256']) == 64



@pytest.mark.parametrize('profile,suffix', [('mov-single-v1','.mov'),('m4v-single-v1','.m4v')])
def test_quicktime_suffix_profiles_are_qualified_separately(tmp_path, profile, suffix):
    r = qualify_exiftool_format(_fake(tmp_path/profile), tmp_path/f'work-{profile}', format_profile_id=profile)
    assert r.passed and Path(r.fixture_path).suffix == suffix and r.format_profile_id == profile

def test_quicktime_mdat_change_is_detected(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path, mode='payload-change'), tmp_path/'work', format_profile_id='mp4-single-v1')
    assert not r.passed and not r.checks['media_payload_unchanged']


def test_format_qualification_binds_distribution_identity(tmp_path):
    dist = tmp_path/'dist'; dist.mkdir(); exe = _fake(dist); (dist/'lib').mkdir(); (dist/'lib/x.pm').write_text('x')
    r = qualify_exiftool_format(exe, tmp_path/'work', format_profile_id='png-single-v1', distribution_root=dist)
    assert r.passed and len(r.distribution_sha256) == 64 and r.checks['distribution_fingerprinted']


def test_format_qualification_report_is_write_once_json(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path), tmp_path/'work', format_profile_id='png-single-v1')
    out = tmp_path/'format-qualification.json'; write_format_qualification_report(out, r)
    obj = json.loads(out.read_text())
    assert obj['schema'] == FORMAT_QUALIFICATION_SCHEMA and obj['qualification_id'] == r.qualification_id
    with pytest.raises(FileExistsError): write_format_qualification_report(out, r)


def test_unsupported_profile_and_nonpositive_timeout_fail_closed(tmp_path):
    exe = _fake(tmp_path)
    with pytest.raises(ValueError): qualify_exiftool_format(exe, tmp_path/'w', format_profile_id='raw-single-v1')
    with pytest.raises(ValueError): qualify_exiftool_format(exe, tmp_path/'w2', format_profile_id='png-single-v1', subprocess_timeout_seconds=0)


def test_hung_candidate_is_bounded(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path, mode='hang'), tmp_path/'work', format_profile_id='png-single-v1', subprocess_timeout_seconds=0.1)
    assert not r.passed and any('timed out' in e.lower() for e in r.errors)


def test_cli_format_qualification_writes_machine_readable_report(tmp_path, capsys):
    from gpa.__main__ import main
    exe = _fake(tmp_path); report = tmp_path/'format.json'
    rc = main(['qualify-exiftool-format','--executable',str(exe),'--workdir',str(tmp_path/'work'),'--format-profile','png-single-v1','--report',str(report)])
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0 and obj['passed'] and obj['harness'] == PNG_HARNESS_ID and report.exists()


def test_cli_jpeg_format_qualification_closes_standalone_jpeg_evidence_lane(tmp_path, capsys):
    from gpa.__main__ import main
    exe = _fake(tmp_path); report = tmp_path/'jpeg-format.json'
    rc = main(['qualify-exiftool-format','--executable',str(exe),'--workdir',str(tmp_path/'work'),'--format-profile','jpeg-single-v1','--report',str(report)])
    obj = json.loads(capsys.readouterr().out)
    saved = json.loads(report.read_text())
    assert rc == 0 and obj['passed'] and saved['passed']
    assert obj['format_profile_id'] == 'jpeg-single-v1' and obj['harness'] == JPEG_HARNESS_ID
