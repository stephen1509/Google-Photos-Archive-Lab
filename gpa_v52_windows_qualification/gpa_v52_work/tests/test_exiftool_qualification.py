from __future__ import annotations

import json
from pathlib import Path

import pytest

from gpa.exiftool import ExifToolError, production_exiftool_adapter
from gpa.exiftool_qualification import (
    QUALIFICATION_SCHEMA,
    fingerprint_exiftool_candidate,
    qualify_exiftool_candidate,
    write_qualification_report,
)


def _fake(path:Path, *, mode='good', version='13.55')->Path:
    p=path/'exiftool-fake.py'
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
    if MODE=='wrong-readback': d['ExifIFD:DateTimeOriginal']='1999:01:01 00:00:00'
    print(json.dumps([d]));raise SystemExit(0)
vals={{}}
for a in sys.argv[1:-1]:
    if a.startswith('-') and '=' in a:
        k,v=a[1:].split('=',1)
        if k=='DateTimeOriginal': vals['ExifIFD:DateTimeOriginal']=v
        elif k=='OffsetTimeOriginal': vals['ExifIFD:OffsetTimeOriginal']=v
        elif k in ('GPSLatitude','GPSLatitudeRef','GPSLongitude','GPSLongitudeRef'): vals['GPS:'+k]=float(v) if k in ('GPSLatitude','GPSLongitude') else v
        elif k=='XMP-photoshop:DateCreated': vals['XMP-photoshop:DateCreated']=v
        elif k in ('dc:description','XMP-dc:Description'): vals['XMP-dc:Description']=v
if MODE!='no-change':
    if MODE=='recompress':
        from PIL import Image
        im=Image.open(file).convert('RGB');px=im.load();px[0,0]=(255,0,0);im.save(file,'JPEG',quality=70)
    else:
        b=file.read_bytes();payload=b'GPA-QUALIFIED';seg=b'\\xff\\xfe'+(len(payload)+2).to_bytes(2,'big')+payload
        file.write_bytes(b[:2]+seg+b[2:])
meta.write_text(json.dumps(vals))
print('1 image files updated')
''')
    p.chmod(0o755);return p


def test_good_candidate_passes_jpeg_roundtrip_and_payload_guard(tmp_path):
    exe=_fake(tmp_path)
    r=qualify_exiftool_candidate(exe,tmp_path/'work')
    assert r.schema==QUALIFICATION_SCHEMA and r.passed
    assert r.version=='13.55' and len(r.executable_sha256)==64 and len(r.qualification_id)==24
    assert r.checks['whole_file_changed'] and r.checks['jpeg_payload_unchanged'] and r.checks['output_decodable']
    assert r.checks['readback_date'] and r.checks['readback_gps'] and r.checks['readback_description']


def test_fake_that_only_prints_success_cannot_pass(tmp_path):
    r=qualify_exiftool_candidate(_fake(tmp_path,mode='no-change'),tmp_path/'work')
    assert not r.passed and not r.checks['whole_file_changed']


def test_candidate_that_recompresses_pixels_cannot_pass(tmp_path):
    r=qualify_exiftool_candidate(_fake(tmp_path,mode='recompress'),tmp_path/'work')
    assert not r.passed and not r.checks['jpeg_payload_unchanged']


def test_wrong_readback_cannot_pass(tmp_path):
    r=qualify_exiftool_candidate(_fake(tmp_path,mode='wrong-readback'),tmp_path/'work')
    assert not r.passed and not r.checks['readback_date']


def test_candidate_fingerprint_rejects_symlink(tmp_path):
    exe=_fake(tmp_path);link=tmp_path/'link'
    try:
        link.symlink_to(exe)
    except OSError as e:
        pytest.skip(f'Windows symlink privilege unavailable; cannot exercise real symlink rejection: {e}')
    with pytest.raises(Exception):fingerprint_exiftool_candidate(link)


def test_distribution_fingerprint_is_recorded_and_symlinks_refused(tmp_path):
    dist=tmp_path/'dist';dist.mkdir();exe=_fake(dist);(dist/'lib').mkdir();(dist/'lib/x.pm').write_text('x')
    r=qualify_exiftool_candidate(exe,tmp_path/'work',distribution_root=dist)
    assert r.passed and len(r.distribution_sha256)==64 and r.checks['distribution_fingerprinted']
    try:
        (dist/'lib/link.pm').symlink_to(tmp_path/'outside')
    except OSError as e:
        pytest.skip(f'Windows symlink privilege unavailable; cannot exercise real symlink rejection: {e}')
    bad=qualify_exiftool_candidate(exe,tmp_path/'work2',distribution_root=dist)
    assert not bad.passed and any('symlink' in e for e in bad.errors)


def test_qualification_report_is_write_once_json(tmp_path):
    r=qualify_exiftool_candidate(_fake(tmp_path),tmp_path/'work')
    out=tmp_path/'report.json';write_qualification_report(out,r)
    obj=json.loads(out.read_text());assert obj['schema']==QUALIFICATION_SCHEMA and obj['qualification_id']==r.qualification_id
    with pytest.raises(FileExistsError):write_qualification_report(out,r)


def test_production_allowlist_remains_empty_and_fails_closed(tmp_path):
    exe=_fake(tmp_path)
    a=production_exiftool_adapter(exe)
    with pytest.raises(ExifToolError):a.version()


def test_cli_qualification_writes_machine_readable_report(tmp_path,capsys):
    from gpa.__main__ import main
    exe=_fake(tmp_path);report=tmp_path/'qualification.json'
    rc=main(['qualify-exiftool','--executable',str(exe),'--workdir',str(tmp_path/'work'),'--report',str(report)])
    obj=json.loads(capsys.readouterr().out)
    assert rc==0 and obj['passed'] and report.exists() and json.loads(report.read_text())['passed']


def test_hung_exiftool_candidate_fails_closed_with_bounded_timeout(tmp_path):
    r=qualify_exiftool_candidate(_fake(tmp_path,mode='hang'),tmp_path/'work',subprocess_timeout_seconds=0.1)
    assert not r.passed and any('timed out' in e.lower() for e in r.errors)
