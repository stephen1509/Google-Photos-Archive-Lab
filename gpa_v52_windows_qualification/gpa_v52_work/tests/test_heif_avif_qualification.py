from __future__ import annotations

import json
from pathlib import Path

import pytest

from gpa.format_roundtrip_qualification import AVIF_HARNESS_ID, HEIC_HARNESS_ID, qualify_exiftool_format
from gpa.qualification_fixtures import HEIC_FIXTURE, QualificationFixtureError, verify_qualification_fixture


def _fake(path: Path, *, mode="good", version="13.55") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    p=path/'exiftool-avif-fake.py'
    p.write_text(f"""#!/usr/bin/env python3
import json,sys
from pathlib import Path
MODE={mode!r}; VERSION={version!r}
if '-ver' in sys.argv:
    print(VERSION);raise SystemExit(0)
file=Path(sys.argv[-1]);meta=Path(str(file)+'.fake-meta.json')
if '-j' in sys.argv:
    d=json.loads(meta.read_text()) if meta.exists() else {{}}
    print(json.dumps([d]));raise SystemExit(0)
vals={{}}
for a in sys.argv[1:-1]:
    if a.startswith('-') and '=' in a:
        k,v=a[1:].split('=',1)
        if k=='XMP-photoshop:DateCreated': vals['XMP-photoshop:DateCreated']=v
        elif k in ('dc:description','XMP-dc:Description'): vals['XMP-dc:Description']=v
if MODE!='no-change':
    if MODE=='payload-change':
        if file.suffix.lower()=='.heic':
            b=bytearray(file.read_bytes());i=b.find(b'mdat')
            if i<0: raise SystemExit('no mdat')
            b[i+8] ^= 1; file.write_bytes(bytes(b))
        else:
            from PIL import Image
            im=Image.open(file).convert('RGB');px=im.load();px[0,0]=(3,5,7);im.save(file,'AVIF',quality=61)
    else:
        b=file.read_bytes();payload=b'GPA-QUAL';box=(8+len(payload)).to_bytes(4,'big')+b'free'+payload
        file.write_bytes(b+box)
meta.write_text(json.dumps(vals))
print('1 files updated')
""")
    p.chmod(0o755)
    return p


def test_good_avif_candidate_passes_item_payload_decode_and_readback(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path), tmp_path/'work', format_profile_id='avif-single-v1')
    assert r.harness == AVIF_HARNESS_ID and r.passed
    assert r.checks['fixture_valid_before'] and r.checks['media_payload_unchanged'] and r.checks['output_decodable']
    assert r.validator['validator'] == 'libheif/heif-convert + Pillow'
    assert len(r.validator['validator_sha256']) == 64


def test_avif_coded_item_payload_change_is_detected(tmp_path):
    r = qualify_exiftool_format(_fake(tmp_path, mode='payload-change'), tmp_path/'work', format_profile_id='avif-single-v1')
    assert not r.passed and not r.checks['media_payload_unchanged']


def _bmff_box(typ: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, 'big') + typ + payload


def _full_box(typ: bytes, version: int, payload: bytes, flags: int = 0) -> bytes:
    return _bmff_box(typ, bytes([version]) + flags.to_bytes(3, 'big') + payload)


def _infe(item_id: int, item_type: bytes, name: bytes, content_type: bytes | None = None) -> bytes:
    payload = item_id.to_bytes(2, 'big') + b'\0\0' + item_type + name + b'\0'
    if item_type == b'mime':
        payload += (content_type or b'application/octet-stream') + b'\0'
    return _full_box(b'infe', 2, payload)


def _synthetic_heif(*, use_idat: bool, construction_method: int | None = None, external_reference: bool = False,
                    image_payload: bytes = b'IMAGE-CODED-PAYLOAD', exif_payload: bytes = b'Exif-data',
                    xmp_payload: bytes = b'<xmp/>', other_mime_payload: bytes | None = None) -> bytes:
    items = [(1, b'av01', b'Color', None, image_payload), (2, b'Exif', b'Exif', None, exif_payload),
             (3, b'mime', b'XMP', b'application/rdf+xml', xmp_payload)]
    if other_mime_payload is not None:
        items.append((4, b'mime', b'Other', b'application/octet-stream', other_mime_payload))
    iinf = _full_box(b'iinf', 0, len(items).to_bytes(2, 'big') + b''.join(_infe(i, t, n, c) for i,t,n,c,_ in items))
    payload_blob = b''.join(row[4] for row in items)
    rel_offsets=[]; off=0
    for row in items:
        rel_offsets.append((off,len(row[4]))); off += len(row[4])
    if use_idat:
        method = 1 if construction_method is None else construction_method
        entries=[]
        for (item_id, *_), (offset,length) in zip(items,rel_offsets):
            entries.append(item_id.to_bytes(2,'big') + method.to_bytes(2,'big') + (1 if external_reference else 0).to_bytes(2,'big') + b'\0\1' + offset.to_bytes(4,'big') + length.to_bytes(4,'big'))
        iloc=_full_box(b'iloc',1,b'\x44\x00'+len(items).to_bytes(2,'big')+b''.join(entries))
        meta=_full_box(b'meta',0,iloc+iinf+_bmff_box(b'idat',payload_blob))
        return _bmff_box(b'ftyp',b'avif\0\0\0\0avif')+meta
    # method 0 offsets are absolute file offsets, so build once for sizing then rebuild.
    def iloc_with(base: int) -> bytes:
        entries=[]
        for (item_id, *_), (offset,length) in zip(items,rel_offsets):
            entries.append(item_id.to_bytes(2,'big') + (1 if external_reference else 0).to_bytes(2,'big') + b'\0\1' + (base+offset).to_bytes(4,'big') + length.to_bytes(4,'big'))
        return _full_box(b'iloc',0,b'\x44\x00'+len(items).to_bytes(2,'big')+b''.join(entries))
    ftyp=_bmff_box(b'ftyp',b'avif\0\0\0\0avif')
    probe_meta=_full_box(b'meta',0,iloc_with(0)+iinf)
    mdat_payload_start=len(ftyp)+len(probe_meta)+8
    meta=_full_box(b'meta',0,iloc_with(mdat_payload_start)+iinf)
    return ftyp+meta+_bmff_box(b'mdat',payload_blob)


def test_heif_item_fingerprint_ignores_only_exif_and_xmp_metadata():
    from gpa.fingerprint import heif_item_payload_fingerprint
    base=_synthetic_heif(use_idat=False)
    changed=_synthetic_heif(use_idat=False, exif_payload=b'new-exif-longer', xmp_payload=b'<new-xmp-longer/>')
    assert heif_item_payload_fingerprint(base) == heif_item_payload_fingerprint(changed)
    changed_image=_synthetic_heif(use_idat=False, image_payload=b'CHANGED-CODED-PAYLOAD')
    assert heif_item_payload_fingerprint(base) != heif_item_payload_fingerprint(changed_image)


def test_heif_item_fingerprint_supports_idat_construction_method_one():
    from gpa.fingerprint import heif_item_payload_fingerprint
    a=_synthetic_heif(use_idat=True)
    b=_synthetic_heif(use_idat=True, exif_payload=b'different-exif')
    assert heif_item_payload_fingerprint(a) == heif_item_payload_fingerprint(b)


def test_heif_unknown_mime_item_is_protected_not_assumed_metadata():
    from gpa.fingerprint import heif_item_payload_fingerprint
    a=_synthetic_heif(use_idat=False, other_mime_payload=b'opaque-one')
    b=_synthetic_heif(use_idat=False, other_mime_payload=b'opaque-two')
    assert heif_item_payload_fingerprint(a) != heif_item_payload_fingerprint(b)


def test_heif_unsupported_construction_and_external_reference_fail_closed():
    from gpa.fingerprint import MediaStructureError, heif_item_payload_fingerprint
    with pytest.raises(MediaStructureError, match='construction method'):
        heif_item_payload_fingerprint(_synthetic_heif(use_idat=True, construction_method=2))
    with pytest.raises(MediaStructureError, match='external'):
        heif_item_payload_fingerprint(_synthetic_heif(use_idat=True, external_reference=True))


def test_real_avif_exif_xmp_changes_do_not_change_protected_item_fingerprint(tmp_path):
    from PIL import Image
    from gpa.fingerprint import heif_item_payload_fingerprint
    im=Image.new('RGB',(16,13))
    pix=im.load()
    for y in range(13):
        for x in range(16): pix[x,y]=((x*17+y*3)%256,(x*5+y*19)%256,(x*11+y*7)%256)
    plain=tmp_path/'plain.avif'; meta=tmp_path/'meta.avif'
    im.save(plain,'AVIF',quality=74)
    ex=Image.Exif(); ex[0x010E]='GPA metadata test'
    im.save(meta,'AVIF',quality=74,exif=ex,xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>')
    assert heif_item_payload_fingerprint(plain.read_bytes()) == heif_item_payload_fingerprint(meta.read_bytes())


HEIC_TEST_FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'heic' / 'rainbow-451x461.heic'


def test_pinned_heic_fixture_exact_provenance_and_real_decode():
    from gpa.media_validation import validate_media
    admitted = verify_qualification_fixture(HEIC_TEST_FIXTURE, HEIC_FIXTURE)
    assert admitted['exact_bytes_verified'] and admitted['source_git_blob_verified'] and admitted['observed_size'] == 7080
    assert admitted['observed_sha256'] == '4b2ce727f093944975f143ba2b39c4c64511b766d94552f8d51a755916e7f983'
    result = validate_media(HEIC_TEST_FIXTURE)
    assert result.status == 'passed' and result.bytes_unchanged is True
    assert result.validator == 'libheif/heif-convert + Pillow'


def test_good_heic_candidate_passes_pinned_fixture_payload_decode_and_readback(tmp_path):
    source_before = HEIC_TEST_FIXTURE.read_bytes()
    r = qualify_exiftool_format(
        _fake(tmp_path), tmp_path/'work', format_profile_id='heic-single-v1',
        qualification_fixture=HEIC_TEST_FIXTURE,
    )
    assert r.harness == HEIC_HARNESS_ID and r.passed
    assert r.checks['fixture_exact_source_verified'] and r.checks['fixture_exact_copy_verified']
    assert r.checks['media_payload_unchanged'] and r.checks['output_decodable']
    assert r.fixture_admission['fixture_id'] == HEIC_FIXTURE.fixture_id
    assert r.fixture_admission['production_write_approved'] is False
    assert HEIC_TEST_FIXTURE.read_bytes() == source_before


def test_heic_payload_mutation_is_detected(tmp_path):
    r = qualify_exiftool_format(
        _fake(tmp_path, mode='payload-change'), tmp_path/'work', format_profile_id='heic-single-v1',
        qualification_fixture=HEIC_TEST_FIXTURE,
    )
    assert not r.passed and not r.checks['media_payload_unchanged']


def test_heic_qualification_requires_exact_pinned_fixture(tmp_path):
    exe = _fake(tmp_path/'fake')
    missing = qualify_exiftool_format(exe, tmp_path/'w0', format_profile_id='heic-single-v1')
    assert not missing.passed and any('requires --fixture' in e for e in missing.errors)
    bad = tmp_path/'bad.heic'; bad.write_bytes(HEIC_TEST_FIXTURE.read_bytes()[:-1])
    wrong = qualify_exiftool_format(exe, tmp_path/'w1', format_profile_id='heic-single-v1', qualification_fixture=bad)
    assert not wrong.passed and any('size mismatch' in e for e in wrong.errors)


def test_heic_fixture_symlink_is_rejected(tmp_path):
    link = tmp_path/'fixture.heic'
    try:
        link.symlink_to(HEIC_TEST_FIXTURE)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    with pytest.raises(QualificationFixtureError, match='symlink'):
        verify_qualification_fixture(link, HEIC_FIXTURE)


def test_heic_cli_requires_and_records_fixture(tmp_path, capsys):
    from gpa.__main__ import main
    exe = _fake(tmp_path/'fake'); report = tmp_path/'heic.json'
    rc = main([
        'qualify-exiftool-format','--executable',str(exe),'--workdir',str(tmp_path/'work'),
        '--format-profile','heic-single-v1','--fixture',str(HEIC_TEST_FIXTURE),'--report',str(report),
    ])
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0 and obj['passed'] and obj['harness'] == HEIC_HARNESS_ID
    assert obj['fixture_admission']['fixture_id'] == HEIC_FIXTURE.fixture_id and report.exists()


def test_heic_fixture_provenance_manifest_and_license_are_self_consistent():
    import hashlib
    provenance_path = HEIC_TEST_FIXTURE.with_name('rainbow-451x461.provenance.json')
    provenance = json.loads(provenance_path.read_text())
    license_path = HEIC_TEST_FIXTURE.with_name(provenance['license_file'])
    assert provenance['fixture_id'] == HEIC_FIXTURE.fixture_id
    assert provenance['source_commit'] == HEIC_FIXTURE.source_commit
    assert provenance['source_git_blob_sha1'] == HEIC_FIXTURE.source_git_blob_sha1
    assert provenance['sha256'] == HEIC_FIXTURE.sha256
    assert provenance['expected_size'] == HEIC_FIXTURE.expected_size
    assert hashlib.sha256(license_path.read_bytes()).hexdigest() == provenance['license_sha256']
    assert provenance['qualification_only'] is True and provenance['production_approval'] is False
