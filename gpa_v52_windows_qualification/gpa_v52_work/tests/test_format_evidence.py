from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

from gpa.exiftool_distribution import EXIFTOOL_DISTRIBUTIONS, ExifToolDistributionSpec, admit_distribution
from gpa.exiftool_evidence import EVIDENCE_SCHEMA, expected_exiftool_evidence_bundle_id
from gpa.format_evidence import FORMAT_EVIDENCE_SCHEMA, build_format_evidence_bundle, bundle_format_evidence_report_files
from gpa.format_roundtrip_qualification import FORMAT_QUALIFICATION_SCHEMA, HEIC_HARNESS_ID, JPEG_HARNESS_ID, PNG_HARNESS_ID, expected_format_qualification_id
from gpa.qualification_fixtures import FIXTURE_SCHEMA, HEIC_FIXTURE


def _zip_bytes() -> bytes:
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('exiftool-test/exiftool(-k).exe',b'EXE-BYTES')
        z.writestr('exiftool-test/exiftool_files/lib/x.pm',b'package x;')
        z.writestr('exiftool-test/README.txt',b'readme')
    return b.getvalue()


def _fixture(tmp_path):
    raw=_zip_bytes()
    spec=ExifToolDistributionSpec(candidate_id='test-format-evidence-win-x64',version='99.2',platform='windows',architecture='x64',filename='candidate.zip',source_url='https://example.invalid/candidate.zip',size_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),top_level_dir='exiftool-test')
    EXIFTOOL_DISTRIBUTIONS[spec.candidate_id]=spec
    archive=tmp_path/'candidate.zip';archive.write_bytes(raw)
    admission=admit_distribution(spec,archive,tmp_path/'prepared')
    base={
        'schema':EVIDENCE_SCHEMA,'bundle_id':'','candidate_id':spec.candidate_id,'version':spec.version,
        'archive_size_bytes':spec.size_bytes,'archive_sha256':spec.sha256,
        'executable_sha256':admission.executable_sha256,'distribution_sha256':admission.prepared_distribution_sha256,
        'qualification_id':'base-qualification','qualification_harness':'gpa.exiftool-jpeg-roundtrip.v1',
        'admission_report_sha256':'1'*64,'qualification_report_sha256':'2'*64,
        'live_archive_size_bytes':spec.size_bytes,'live_archive_sha256':spec.sha256,
        'live_executable_sha256':admission.executable_sha256,'live_distribution_sha256':admission.prepared_distribution_sha256,
        'ready_for_promotion_review':True,'production_write_approved':False,'checks':{'all_prior_checks':True},
    }
    base['bundle_id']=expected_exiftool_evidence_bundle_id(base)
    fq={
        'schema':FORMAT_QUALIFICATION_SCHEMA,'harness':PNG_HARNESS_ID,'format_profile_id':'png-single-v1',
        'qualification_id':'','passed':True,'version':spec.version,
        'executable_sha256':admission.executable_sha256,'distribution_sha256':admission.prepared_distribution_sha256,
        'validator':{'validator':'Pillow','version':'11.0','validator_sha256':'c'*64},
        'checks':{'validator_identified':True,'validator_build_fingerprinted':True,'media_payload_unchanged':True,'output_decodable':True,'readback_completed':True,'readback_description':True,'readback_xmp_date':True},
        'errors':[],
    }
    fq['qualification_id']=expected_format_qualification_id(fq)
    bp=tmp_path/'base.json';fp=tmp_path/'format.json';bp.write_text(json.dumps(base));fp.write_text(json.dumps(fq))
    return spec,archive,Path(admission.prepared_distribution_root),Path(admission.executable_path),bp,fp,base,fq


def test_in_memory_bundle_is_not_ready_without_bound_reports_and_live_artifacts(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        b=build_format_evidence_bundle(base,fq)
        assert b.schema==FORMAT_EVIDENCE_SCHEMA and not b.ready_for_format_promotion_review and not b.production_write_approved
        assert b.checks['format_harness_registered'] and b.checks['executable_identity_matches']
        assert not b.checks['base_evidence_report_bound'] and not b.checks['live_archive_sha256_matches_pin']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_live_file_bundle_reverifies_all_artifacts_and_is_review_ready(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        b=bundle_format_evidence_report_files(bp,fp,archive_path=archive,distribution_root=root,executable_path=exe)
        assert b.ready_for_format_promotion_review and not b.production_write_approved
        assert b.format_profile_id=='png-single-v1' and b.format_qualification_harness==PNG_HARNESS_ID
        assert len(b.base_evidence_report_sha256)==64 and len(b.format_qualification_report_sha256)==64
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_wrong_harness_or_failed_format_qualification_blocks_review(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        fq['harness']='wrong';fq['passed']=False
        b=build_format_evidence_bundle(fq if False else base,fq,base_evidence_report_sha256='a'*64,format_qualification_report_sha256='b'*64,live_archive_size_bytes=spec.size_bytes,live_archive_sha256=spec.sha256,live_executable_sha256=base['executable_sha256'],live_distribution_sha256=base['distribution_sha256'])
        assert not b.ready_for_format_promotion_review and not b.checks['format_harness_registered'] and not b.checks['format_qualification_passed']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_candidate_or_distribution_identity_mismatch_blocks_review(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        fq['executable_sha256']='d'*64;fq['distribution_sha256']='e'*64
        b=build_format_evidence_bundle(base,fq,base_evidence_report_sha256='a'*64,format_qualification_report_sha256='b'*64,live_archive_size_bytes=spec.size_bytes,live_archive_sha256=spec.sha256,live_executable_sha256=base['executable_sha256'],live_distribution_sha256=base['distribution_sha256'])
        assert not b.ready_for_format_promotion_review and not b.checks['executable_identity_matches'] and not b.checks['distribution_identity_matches']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_validator_and_payload_contract_must_be_explicitly_present(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        fq['checks']['validator_build_fingerprinted']=False;fq['checks']['media_payload_unchanged']=False
        b=build_format_evidence_bundle(base,fq,base_evidence_report_sha256='a'*64,format_qualification_report_sha256='b'*64,live_archive_size_bytes=spec.size_bytes,live_archive_sha256=spec.sha256,live_executable_sha256=base['executable_sha256'],live_distribution_sha256=base['distribution_sha256'])
        assert not b.ready_for_format_promotion_review and not b.checks['validator_build_fingerprinted'] and not b.checks['media_payload_unchanged']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_live_distribution_tamper_is_detected(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        (root/'exiftool_files/lib/x.pm').write_text('tampered')
        b=bundle_format_evidence_report_files(bp,fp,archive_path=archive,distribution_root=root,executable_path=exe)
        assert not b.ready_for_format_promotion_review and not b.checks['live_distribution_identity_matches']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_tampered_base_bundle_id_blocks_review(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        base['bundle_id']='0'*24
        b=build_format_evidence_bundle(base,fq,base_evidence_report_sha256='a'*64,format_qualification_report_sha256='b'*64,live_archive_size_bytes=spec.size_bytes,live_archive_sha256=spec.sha256,live_executable_sha256=base['executable_sha256'],live_distribution_sha256=base['distribution_sha256'])
        assert not b.ready_for_format_promotion_review and not b.checks['base_evidence_bundle_id_valid']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_tampered_format_qualification_id_blocks_review(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        fq['qualification_id']='f'*24
        b=build_format_evidence_bundle(base,fq,base_evidence_report_sha256='a'*64,format_qualification_report_sha256='b'*64,live_archive_size_bytes=spec.size_bytes,live_archive_sha256=spec.sha256,live_executable_sha256=base['executable_sha256'],live_distribution_sha256=base['distribution_sha256'])
        assert not b.ready_for_format_promotion_review and not b.checks['format_qualification_id_valid']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_cli_writes_review_ready_but_never_production_approved_bundle(tmp_path,capsys):
    from gpa.__main__ import main
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path);out=tmp_path/'format-bundle.json'
    try:
        rc=main(['bundle-exiftool-format-evidence','--base-evidence',str(bp),'--format-qualification',str(fp),'--archive',str(archive),'--distribution-root',str(root),'--executable',str(exe),'--output',str(out)])
        obj=json.loads(out.read_text())
        assert rc==0 and obj['ready_for_format_promotion_review'] is True and obj['production_write_approved'] is False
        assert json.loads(capsys.readouterr().out)['bundle_id']==obj['bundle_id']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def _as_heic_format_qualification(fq: dict) -> dict:
    fq = json.loads(json.dumps(fq))
    fq['harness'] = HEIC_HARNESS_ID
    fq['format_profile_id'] = HEIC_FIXTURE.format_profile_id
    fq['fixture_admission'] = {
        'schema': FIXTURE_SCHEMA,
        'fixture_id': HEIC_FIXTURE.fixture_id,
        'format_profile_id': HEIC_FIXTURE.format_profile_id,
        'expected_sha256': HEIC_FIXTURE.sha256,
        'observed_sha256': HEIC_FIXTURE.sha256,
        'source_commit': HEIC_FIXTURE.source_commit,
        'source_git_blob_sha1': HEIC_FIXTURE.source_git_blob_sha1,
        'observed_git_blob_sha1': HEIC_FIXTURE.source_git_blob_sha1,
        'license': HEIC_FIXTURE.license,
        'exact_bytes_verified': True,
        'source_git_blob_verified': True,
        'production_write_approved': False,
    }
    fq['qualification_id'] = expected_format_qualification_id(fq)
    return fq


def test_heic_format_evidence_binds_exact_external_fixture_identity(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        fq = _as_heic_format_qualification(fq)
        b=build_format_evidence_bundle(
            base,fq,
            base_evidence_report_sha256='a'*64,
            format_qualification_report_sha256='b'*64,
            live_archive_size_bytes=spec.size_bytes,
            live_archive_sha256=spec.sha256,
            live_executable_sha256=base['executable_sha256'],
            live_distribution_sha256=base['distribution_sha256'],
        )
        assert b.ready_for_format_promotion_review and not b.production_write_approved
        assert b.checks['qualification_fixture_requirement_satisfied']
        assert b.qualification_fixture_id == HEIC_FIXTURE.fixture_id
        assert b.qualification_fixture_sha256 == HEIC_FIXTURE.sha256
        assert b.qualification_fixture_source_commit == HEIC_FIXTURE.source_commit
        assert b.qualification_fixture_git_blob_sha1 == HEIC_FIXTURE.source_git_blob_sha1
        assert b.qualification_fixture_license == HEIC_FIXTURE.license
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_heic_format_evidence_rejects_missing_or_tampered_fixture_admission(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        fq = _as_heic_format_qualification(fq)
        fq['fixture_admission']['observed_sha256'] = '0'*64
        fq['fixture_admission']['observed_git_blob_sha1'] = '0'*40
        fq['qualification_id'] = expected_format_qualification_id(fq)
        b=build_format_evidence_bundle(
            base,fq,
            base_evidence_report_sha256='a'*64,
            format_qualification_report_sha256='b'*64,
            live_archive_size_bytes=spec.size_bytes,
            live_archive_sha256=spec.sha256,
            live_executable_sha256=base['executable_sha256'],
            live_distribution_sha256=base['distribution_sha256'],
        )
        assert not b.ready_for_format_promotion_review
        assert not b.checks['qualification_fixture_requirement_satisfied']

        fq.pop('fixture_admission')
        fq['qualification_id'] = expected_format_qualification_id(fq)
        b2=build_format_evidence_bundle(
            base,fq,
            base_evidence_report_sha256='a'*64,
            format_qualification_report_sha256='b'*64,
            live_archive_size_bytes=spec.size_bytes,
            live_archive_sha256=spec.sha256,
            live_executable_sha256=base['executable_sha256'],
            live_distribution_sha256=base['distribution_sha256'],
        )
        assert not b2.ready_for_format_promotion_review
        assert not b2.checks['qualification_fixture_requirement_satisfied']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_jpeg_format_evidence_can_be_review_ready_under_same_exact_build_chain(tmp_path):
    spec,archive,root,exe,bp,fp,base,fq=_fixture(tmp_path)
    try:
        fq = json.loads(json.dumps(fq))
        fq['harness'] = JPEG_HARNESS_ID
        fq['format_profile_id'] = 'jpeg-single-v1'
        fq['qualification_id'] = expected_format_qualification_id(fq)
        b=build_format_evidence_bundle(
            base,fq,
            base_evidence_report_sha256='a'*64,
            format_qualification_report_sha256='b'*64,
            live_archive_size_bytes=spec.size_bytes,
            live_archive_sha256=spec.sha256,
            live_executable_sha256=base['executable_sha256'],
            live_distribution_sha256=base['distribution_sha256'],
        )
        assert b.ready_for_format_promotion_review and not b.production_write_approved
        assert b.format_profile_id == 'jpeg-single-v1'
        assert b.format_qualification_harness == JPEG_HARNESS_ID
        assert b.checks['format_harness_registered'] and b.checks['format_qualification_id_valid']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)
