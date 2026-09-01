from __future__ import annotations

import hashlib

from gpa.format_qualification import (
    FORMAT_PROFILES,
    LIVE_PHOTO,
    MOTION_PHOTO,
    STANDALONE,
    ProductionFormatApproval,
    authorize_format_write,
    format_write_matrix,
    profile_for_suffix,
)

EXE = hashlib.sha256(b'exe').hexdigest()
DIST = hashlib.sha256(b'dist').hexdigest()
EVIDENCE = hashlib.sha256(b'format-evidence').hexdigest()[:24]
REL_EVIDENCE = hashlib.sha256(b'relationship-evidence').hexdigest()[:24]


def approval(profile: str, relationship: str | None = None) -> ProductionFormatApproval:
    return ProductionFormatApproval('13.55', EXE, DIST, profile, relationship, EVIDENCE, REL_EVIDENCE if relationship else None)


def test_matrix_is_explicit_and_fail_closed():
    obj = format_write_matrix()
    assert obj['schema'] == 'gpa.format-write-matrix.v8'
    assert obj['production_policy']['requires_format_approval'] is True
    assert obj['production_policy']['requires_relationship_evidence_bundle'] is True
    assert obj['production_policy']['composite_relationships_require_separate_approval'] is True
    assert obj['production_policy']['unknown_formats_fail_closed'] is True
    motion=next(x for x in obj['relationships'] if x['profile_id']=='motion-photo-composite-v1')
    assert motion['qualification_harness']=='gpa.exiftool-motion-photo-roundtrip.v2' and motion['qualification_ready'] is False
    live=next(x for x in obj['relationships'] if x['profile_id']=='live-photo-pair-v1')
    assert live['qualification_harness']=='gpa.exiftool-live-photo-roundtrip.v1' and live['qualification_ready'] is False
    assert {p['profile_id'] for p in obj['formats']} >= {
        'jpeg-single-v1', 'png-single-v1', 'mov-single-v1', 'mp4-single-v1', 'm4v-single-v1', 'avif-single-v1', 'heic-single-v1', 'heif-generic-single-v1', 'raw-single-v1'
    }


def test_only_jpeg_currently_has_complete_roundtrip_contract():
    ready = {p.profile_id for p in FORMAT_PROFILES if p.qualification_ready}
    assert ready == {'jpeg-single-v1'}
    assert profile_for_suffix('.png').payload_fingerprint == 'png-visual-payload-v1'
    assert profile_for_suffix('.mp4').payload_fingerprint == 'iso-bmff-mdat-v1'
    assert profile_for_suffix('.heic').payload_fingerprint == 'heif-item-payload-v1'
    assert profile_for_suffix('.avif').payload_fingerprint == 'heif-item-payload-v1'
    assert profile_for_suffix('.heif').profile_id == 'heif-generic-single-v1'
    assert profile_for_suffix('.dng').payload_fingerprint is None


def test_exact_jpeg_approval_can_authorize_only_standalone_jpeg():
    a = approval('jpeg-single-v1')
    d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=DIST,
                               path='photo.jpg', relationship_kind=STANDALONE, approvals=[a])
    assert d.allowed and d.format_profile_id == 'jpeg-single-v1' and d.relationship_profile_id is None


def test_jpeg_approval_does_not_authorize_png_or_video():
    a = approval('jpeg-single-v1')
    for path in ('image.png', 'movie.mp4'):
        d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=DIST,
                                   path=path, relationship_kind=STANDALONE, approvals=[a])
        assert not d.allowed



def test_video_suffix_approvals_do_not_spill_across_mov_mp4_m4v():
    mp4 = approval('mp4-single-v1')
    for path in ('clip.mov','clip.m4v'):
        d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=DIST,
                                   path=path, relationship_kind=STANDALONE, approvals=[mp4])
        assert not d.allowed and d.format_profile_id != 'mp4-single-v1'

def test_unfinished_format_cannot_be_enabled_even_with_forged_approval_entry():
    a = approval('avif-single-v1')
    d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=DIST,
                               path='photo.avif', relationship_kind=STANDALONE, approvals=[a])
    assert not d.allowed and any('production candidate' in x for x in d.reasons)


def test_motion_photo_overrides_plain_jpeg_approval():
    plain = approval('jpeg-single-v1')
    d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=DIST,
                               path='motion.jpg', relationship_kind=MOTION_PHOTO, approvals=[plain])
    assert not d.allowed
    assert d.relationship_profile_id == 'motion-photo-composite-v1'
    assert any('Motion Photo' in x for x in d.reasons)


def test_live_photo_overrides_plain_mov_scope():
    # Even a hypothetical QuickTime approval is insufficient while the relationship
    # profile itself is unqualified.
    q = approval('mov-single-v1')
    d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=DIST,
                               path='IMG_1234.MOV', relationship_kind=LIVE_PHOTO, approvals=[q])
    assert not d.allowed and d.relationship_profile_id == 'live-photo-pair-v1'


def test_unknown_relationship_fails_closed():
    a = approval('jpeg-single-v1')
    d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=DIST,
                               path='photo.jpg', relationship_kind='unknown', approvals=[a])
    assert not d.allowed and any('unknown relationship' in x for x in d.reasons)


def test_missing_distribution_identity_fails_closed():
    a = approval('jpeg-single-v1')
    d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=None,
                               path='photo.jpg', relationship_kind=STANDALONE, approvals=[a])
    assert not d.allowed and any('distribution' in x for x in d.reasons)


def test_candidate_identity_must_match_exactly():
    a = approval('jpeg-single-v1')
    for version, exe, dist in (
        ('13.59', EXE, DIST),
        ('13.55', hashlib.sha256(b'other').hexdigest(), DIST),
        ('13.55', EXE, hashlib.sha256(b'other-dist').hexdigest()),
    ):
        d = authorize_format_write(candidate_version=version, executable_sha256=exe, distribution_sha256=dist,
                                   path='photo.jpg', relationship_kind=STANDALONE, approvals=[a])
        assert not d.allowed


def test_unknown_extension_is_not_mapped_by_writability_guess():
    assert profile_for_suffix('photo.jxl') is None
    d = authorize_format_write(candidate_version='13.55', executable_sha256=EXE, distribution_sha256=DIST,
                               path='photo.jxl', relationship_kind=STANDALONE, approvals=[])
    assert not d.allowed and d.format_profile_id is None


def test_approval_requires_real_sha256_shape():
    import pytest
    with pytest.raises(ValueError):
        ProductionFormatApproval('13.55', 'bad', DIST, 'jpeg-single-v1', None, EVIDENCE)



def test_production_format_approval_requires_reviewed_format_evidence_bundle_id():
    import pytest
    with pytest.raises(ValueError, match='format_evidence_bundle_id'):
        ProductionFormatApproval('13.55', EXE, DIST, 'jpeg-single-v1')
    with pytest.raises(ValueError, match='format_evidence_bundle_id'):
        ProductionFormatApproval('13.55', EXE, DIST, 'jpeg-single-v1', None, 'not-a-bundle')

def _fake_exiftool_distribution(tmp_path, version='13.55'):
    import os
    root=tmp_path/'dist';root.mkdir()
    exe=root/'exiftool.exe'
    exe.write_text('#!/usr/bin/env python3\nimport sys\nif "-ver" in sys.argv: print("'+version+'")\nelse: print("ok")\n')
    os.chmod(exe,0o755)
    (root/'exiftool_files').mkdir();(root/'exiftool_files'/'marker.txt').write_text('support')
    return root,exe


def test_production_adapter_requires_format_approval_in_addition_to_build(tmp_path):
    from gpa.exiftool import ExifToolError, ProductionExifToolAdapter
    from gpa.exiftool_distribution import distribution_tree_sha256
    import pytest
    root,exe=_fake_exiftool_distribution(tmp_path)
    exe_sha=hashlib.sha256(exe.read_bytes()).hexdigest();dist_sha=distribution_tree_sha256(root)
    a=ProductionExifToolAdapter(exe,distribution_root=root,approved_builds={("13.55",exe_sha)},format_approvals=[])
    with pytest.raises(ExifToolError,match='not approved'):
        a.write(tmp_path/'photo.jpg',__import__('gpa.writepolicy',fromlist=['MetadataPlan']).MetadataPlan(),relationship_kind=STANDALONE)
    ok=ProductionFormatApproval('13.55',exe_sha,dist_sha,'jpeg-single-v1',None,EVIDENCE)
    a=ProductionExifToolAdapter(exe,distribution_root=root,approved_builds={("13.55",exe_sha)},format_approvals=[ok])
    assert a.write_authorization(tmp_path/'photo.jpg',relationship_kind=STANDALONE).allowed
    assert not a.write_authorization(tmp_path/'photo.png',relationship_kind=STANDALONE).allowed


def test_production_adapter_requires_distribution_identity(tmp_path):
    from gpa.exiftool import ProductionExifToolAdapter
    root,exe=_fake_exiftool_distribution(tmp_path)
    exe_sha=hashlib.sha256(exe.read_bytes()).hexdigest()
    # Exact build alone can pass version(), but lack of a prepared-distribution root
    # must keep every production write unauthorized.
    a=ProductionExifToolAdapter(exe,approved_builds={("13.55",exe_sha)},format_approvals=[])
    d=a.write_authorization(tmp_path/'photo.jpg',relationship_kind=STANDALONE)
    assert not d.allowed and any('distribution' in x for x in d.reasons)


def test_composite_approval_requires_relationship_evidence_bundle():
    import pytest
    with pytest.raises(ValueError):
        ProductionFormatApproval('13.55', EXE, DIST, 'jpeg-single-v1', 'motion-photo-composite-v1', EVIDENCE)
    with pytest.raises(ValueError):
        ProductionFormatApproval('13.55', EXE, DIST, 'jpeg-single-v1', None, EVIDENCE, REL_EVIDENCE)
    ok=ProductionFormatApproval('13.55', EXE, DIST, 'jpeg-single-v1', 'motion-photo-composite-v1', EVIDENCE, REL_EVIDENCE)
    assert ok.relationship_evidence_bundle_id == REL_EVIDENCE


def test_production_write_api_requires_relationship_classification(tmp_path):
    from gpa.exiftool import ProductionExifToolAdapter
    import pytest
    root,exe=_fake_exiftool_distribution(tmp_path)
    exe_sha=hashlib.sha256(exe.read_bytes()).hexdigest()
    a=ProductionExifToolAdapter(exe,distribution_root=root,approved_builds={("13.55",exe_sha)},format_approvals=[])
    with pytest.raises(TypeError):
        a.write(tmp_path/'photo.jpg',__import__('gpa.writepolicy',fromlist=['MetadataPlan']).MetadataPlan())


def test_format_write_matrix_cli_is_machine_readable(capsys):
    import json
    from gpa.__main__ import main
    rc=main(['format-write-matrix'])
    obj=json.loads(capsys.readouterr().out)
    assert rc==0 and obj['schema']=='gpa.format-write-matrix.v8'
    jpeg=next(x for x in obj['formats'] if x['profile_id']=='jpeg-single-v1')
    avif=next(x for x in obj['formats'] if x['profile_id']=='avif-single-v1')
    heic=next(x for x in obj['formats'] if x['profile_id']=='heic-single-v1')
    assert jpeg['qualification_ready'] is True and avif['qualification_ready'] is False and heic['qualification_ready'] is False
