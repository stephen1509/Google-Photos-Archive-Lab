from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gpa.exiftool_distribution import distribution_tree_sha256
from gpa.format_evidence import FORMAT_EVIDENCE_SCHEMA, expected_format_evidence_bundle_id
from gpa.relationship_evidence import (
    RELATIONSHIP_EVIDENCE_SCHEMA,
    build_relationship_evidence_bundle,
    bundle_relationship_evidence_report_files,
    expected_relationship_evidence_bundle_id,
    write_relationship_evidence_bundle,
)
from gpa.relationship_roundtrip_qualification import (
    RELATIONSHIP_QUALIFICATION_SCHEMA,
    MOTION_PHOTO_HARNESS_ID,
    MOTION_PHOTO_PROFILE_ID,
    JPEG_PROFILE_ID,
    MOV_PROFILE_ID,
    LIVE_PHOTO_HARNESS_ID,
    LIVE_PHOTO_PROFILE_ID,
    expected_relationship_qualification_id,
)


def _inputs(tmp_path: Path):
    root=tmp_path/'dist';root.mkdir()
    exe=root/'exiftool.exe';exe.write_bytes(b'exact-exiftool-test-bytes')
    (root/'exiftool_files').mkdir();(root/'exiftool_files/lib.pm').write_bytes(b'library')
    exe_sha=hashlib.sha256(exe.read_bytes()).hexdigest();dist_sha=distribution_tree_sha256(root)
    fe={
        'schema':FORMAT_EVIDENCE_SCHEMA,'bundle_id':'','base_evidence_bundle_id':'a'*24,
        'candidate_id':'exiftool-13.55-win-x64','version':'13.55','executable_sha256':exe_sha,
        'distribution_sha256':dist_sha,'format_profile_id':JPEG_PROFILE_ID,
        'format_qualification_id':'b'*24,'format_qualification_harness':'gpa.exiftool-jpeg-roundtrip.v1',
        'qualification_fixture_id':None,'qualification_fixture_sha256':None,
        'qualification_fixture_source_commit':None,'qualification_fixture_git_blob_sha1':None,
        'qualification_fixture_license':None,'validator':{'validator':'Pillow','version':'12.3.0','validator_sha256':'c'*64},
        'base_evidence_report_sha256':'d'*64,'format_qualification_report_sha256':'e'*64,
        'live_archive_size_bytes':111,'live_archive_sha256':'f'*64,'live_executable_sha256':exe_sha,
        'live_distribution_sha256':dist_sha,'checks':{'all':True},
        'ready_for_format_promotion_review':True,'production_write_approved':False,
    }
    fe['bundle_id']=expected_format_evidence_bundle_id(fe)
    rq_checks={
        'version_nonempty':True,'executable_fingerprinted':True,'fixture_created':True,
        'fixture_motion_structure_valid':True,'fixture_uses_motion_photo_v1_not_legacy':True,
        'fixture_still_decodable':True,'fixture_video_decodable':True,'write_completed':True,
        'whole_file_changed':True,'relationship_structure_preserved':True,'still_payload_unchanged':True,
        'video_bytes_unchanged':True,'video_mdat_unchanged':True,'video_length_preserved':True,
        'video_terminal_after_write':True,'motion_v1_not_legacy_after_write':True,
        'still_decodable_after_write':True,'video_decodable_after_write':True,
        'still_validator_fingerprinted':True,'video_validator_fingerprinted':True,
        'readback_completed':True,'readback_description':True,'readback_xmp_date':True,
    }
    rq={
        'schema':RELATIONSHIP_QUALIFICATION_SCHEMA,'harness':MOTION_PHOTO_HARNESS_ID,
        'relationship_profile_id':MOTION_PHOTO_PROFILE_ID,'format_profile_id':JPEG_PROFILE_ID,
        'qualification_id':'','passed':True,'version':'13.55','executable_sha256':exe_sha,
        'distribution_sha256':dist_sha,'checks':rq_checks,'errors':[],'production_write_approved':False,
    }
    rq['qualification_id']=expected_relationship_qualification_id(rq)
    return root,exe,fe,rq



def _live_inputs(tmp_path: Path):
    root,exe,fe,_=_inputs(tmp_path)
    sfe=dict(fe)
    sfe['format_profile_id']=MOV_PROFILE_ID
    sfe['format_qualification_harness']='gpa.exiftool-quicktime-roundtrip.v1'
    sfe['validator']={'validator':'ffprobe','version':'8.0','validator_sha256':'9'*64}
    sfe['bundle_id']=expected_format_evidence_bundle_id(sfe)
    checks={
        'version_nonempty':True,'executable_fingerprinted':True,'fixture_created':True,
        'fixture_pair_identity_valid':True,'fixture_components_decodable':True,'fixture_timing_resource_valid':True,
        'write_completed':True,'still_file_changed':True,'movie_file_changed':True,
        'relationship_structure_preserved':True,'content_identifier_preserved':True,'timing_resource_preserved':True,
        'still_payload_unchanged':True,'video_mdat_unchanged':True,'video_bytes_unchanged':True,
        'still_decodable_after_write':True,'video_decodable_after_write':True,
        'still_validator_fingerprinted':True,'video_validator_fingerprinted':True,
        'readback_completed':True,'readback_description':True,'readback_xmp_date':True,
    }
    rq={
        'schema':RELATIONSHIP_QUALIFICATION_SCHEMA,'harness':LIVE_PHOTO_HARNESS_ID,
        'relationship_profile_id':LIVE_PHOTO_PROFILE_ID,'format_profile_id':JPEG_PROFILE_ID,
        'secondary_format_profile_id':MOV_PROFILE_ID,'qualification_id':'','passed':True,
        'version':'13.55','executable_sha256':fe['executable_sha256'],
        'distribution_sha256':fe['distribution_sha256'],'checks':checks,'errors':[],
        'production_write_approved':False,
    }
    rq['qualification_id']=expected_relationship_qualification_id(rq)
    return root,exe,fe,sfe,rq

def test_relationship_evidence_is_review_ready_but_never_approval(tmp_path):
    root,exe,fe,rq=_inputs(tmp_path)
    b=build_relationship_evidence_bundle(
        fe,rq,format_evidence_report_sha256='1'*64,relationship_qualification_report_sha256='2'*64,
        live_executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),live_distribution_sha256=distribution_tree_sha256(root),
    )
    assert b.schema==RELATIONSHIP_EVIDENCE_SCHEMA and b.ready_for_relationship_promotion_review
    assert b.production_write_approved is False
    assert b.bundle_id==expected_relationship_evidence_bundle_id(b.to_dict())
    assert b.checks['relationship_structure_preserved'] and b.checks['video_bytes_unchanged']


def test_tampered_format_bundle_or_relationship_id_blocks_review(tmp_path):
    root,exe,fe,rq=_inputs(tmp_path)
    fe['bundle_id']='0'*24;rq['qualification_id']='f'*24
    b=build_relationship_evidence_bundle(
        fe,rq,format_evidence_report_sha256='1'*64,relationship_qualification_report_sha256='2'*64,
        live_executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),live_distribution_sha256=distribution_tree_sha256(root),
    )
    assert not b.ready_for_relationship_promotion_review
    assert not b.checks['format_evidence_bundle_id_valid'] and not b.checks['relationship_qualification_id_valid']


def test_relationship_damage_or_identity_mismatch_blocks_review(tmp_path):
    root,exe,fe,rq=_inputs(tmp_path)
    rq['checks']['video_bytes_unchanged']=False
    rq['executable_sha256']='0'*64
    rq['qualification_id']=expected_relationship_qualification_id(rq)
    b=build_relationship_evidence_bundle(
        fe,rq,format_evidence_report_sha256='1'*64,relationship_qualification_report_sha256='2'*64,
        live_executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),live_distribution_sha256=distribution_tree_sha256(root),
    )
    assert not b.ready_for_relationship_promotion_review
    assert not b.checks['video_bytes_unchanged'] and not b.checks['executable_identity_matches']


def test_report_file_bundle_rehashes_live_candidate_and_write_once(tmp_path):
    root,exe,fe,rq=_inputs(tmp_path)
    fp=tmp_path/'format-evidence.json';rp=tmp_path/'relationship.json'
    fp.write_text(json.dumps(fe));rp.write_text(json.dumps(rq))
    b=bundle_relationship_evidence_report_files(fp,rp,distribution_root=root,executable_path=exe)
    assert b.ready_for_relationship_promotion_review
    out=tmp_path/'relationship-evidence.json';write_relationship_evidence_bundle(out,b)
    with pytest.raises(FileExistsError):write_relationship_evidence_bundle(out,b)
    (root/'exiftool_files/lib.pm').write_bytes(b'tampered')
    b2=bundle_relationship_evidence_report_files(fp,rp,distribution_root=root,executable_path=exe)
    assert not b2.ready_for_relationship_promotion_review and not b2.checks['live_distribution_identity_matches']


def test_unknown_relationship_profile_fails_closed(tmp_path):
    root,exe,fe,rq=_inputs(tmp_path)
    rq['relationship_profile_id']='future-unknown-v1';rq['qualification_id']=expected_relationship_qualification_id(rq)
    b=build_relationship_evidence_bundle(fe,rq)
    assert not b.ready_for_relationship_promotion_review and b.errors


def test_relationship_evidence_cli_writes_review_ready_bundle(tmp_path, capsys):
    from gpa.__main__ import main
    root,exe,fe,rq=_inputs(tmp_path)
    fp=tmp_path/'format-evidence.json';rp=tmp_path/'relationship.json';out=tmp_path/'bundle.json'
    fp.write_text(json.dumps(fe));rp.write_text(json.dumps(rq))
    rc=main(['bundle-exiftool-relationship-evidence','--format-evidence',str(fp),
             '--relationship-qualification',str(rp),'--distribution-root',str(root),
             '--executable',str(exe),'--output',str(out)])
    obj=json.loads(capsys.readouterr().out)
    assert rc==0 and obj['ready_for_relationship_promotion_review'] is True
    assert json.loads(out.read_text())['bundle_id']==obj['bundle_id']


def test_live_photo_relationship_evidence_binds_both_component_formats(tmp_path):
    root,exe,fe,sfe,rq=_live_inputs(tmp_path)
    b=build_relationship_evidence_bundle(
        fe,rq,secondary_format_evidence=sfe,
        format_evidence_report_sha256='1'*64,secondary_format_evidence_report_sha256='3'*64,
        relationship_qualification_report_sha256='2'*64,
        live_executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
        live_distribution_sha256=distribution_tree_sha256(root),
    )
    assert b.ready_for_relationship_promotion_review
    assert b.format_profile_id==JPEG_PROFILE_ID and b.secondary_format_profile_id==MOV_PROFILE_ID
    assert b.checks['secondary_format_evidence_bundle_id_valid']
    assert b.checks['secondary_candidate_id_matches'] and b.checks['secondary_executable_identity_matches']
    assert b.checks['content_identifier_preserved'] and b.checks['timing_resource_preserved']
    assert b.production_write_approved is False


def test_live_photo_relationship_evidence_fails_without_or_with_wrong_secondary_format(tmp_path):
    root,exe,fe,sfe,rq=_live_inputs(tmp_path)
    b=build_relationship_evidence_bundle(
        fe,rq,format_evidence_report_sha256='1'*64,relationship_qualification_report_sha256='2'*64,
        live_executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),live_distribution_sha256=distribution_tree_sha256(root),
    )
    assert not b.ready_for_relationship_promotion_review and not b.checks['secondary_format_evidence_present']
    bad=dict(sfe);bad['format_profile_id']='mp4-single-v1';bad['bundle_id']=expected_format_evidence_bundle_id(bad)
    b2=build_relationship_evidence_bundle(
        fe,rq,secondary_format_evidence=bad,format_evidence_report_sha256='1'*64,
        secondary_format_evidence_report_sha256='3'*64,relationship_qualification_report_sha256='2'*64,
        live_executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),live_distribution_sha256=distribution_tree_sha256(root),
    )
    assert not b2.ready_for_relationship_promotion_review and not b2.checks['secondary_format_profile_matches']


def test_live_photo_relationship_evidence_cli_requires_and_binds_secondary_report(tmp_path,capsys):
    from gpa.__main__ import main
    root,exe,fe,sfe,rq=_live_inputs(tmp_path)
    fp=tmp_path/'jpeg-format-evidence.json';sp=tmp_path/'mov-format-evidence.json';rp=tmp_path/'relationship-live.json';out=tmp_path/'bundle-live.json'
    fp.write_text(json.dumps(fe));sp.write_text(json.dumps(sfe));rp.write_text(json.dumps(rq))
    rc_missing=main(['bundle-exiftool-relationship-evidence','--format-evidence',str(fp),
                     '--relationship-qualification',str(rp),'--distribution-root',str(root),
                     '--executable',str(exe),'--output',str(tmp_path/'missing.json')])
    missing=json.loads(capsys.readouterr().out)
    assert rc_missing==2 and 'secondary' in missing['error'].lower()
    rc=main(['bundle-exiftool-relationship-evidence','--format-evidence',str(fp),
             '--secondary-format-evidence',str(sp),'--relationship-qualification',str(rp),
             '--distribution-root',str(root),'--executable',str(exe),'--output',str(out)])
    obj=json.loads(capsys.readouterr().out)
    assert rc==0 and obj['ready_for_relationship_promotion_review'] is True
    assert obj['secondary_format_profile_id']==MOV_PROFILE_ID and out.exists()
