from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

from gpa.exiftool_distribution import EXIFTOOL_DISTRIBUTIONS, ExifToolDistributionSpec, admit_distribution
from gpa.exiftool_evidence import EVIDENCE_SCHEMA, build_exiftool_evidence_bundle, bundle_exiftool_report_files
from gpa.exiftool_qualification import HARNESS_ID, QUALIFICATION_SCHEMA


def _zip_bytes() -> bytes:
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('exiftool-test/exiftool(-k).exe',b'EXE-BYTES')
        z.writestr('exiftool-test/exiftool_files/lib/x.pm',b'package x;')
        z.writestr('exiftool-test/README.txt',b'readme')
    return b.getvalue()


def _synthetic_spec(raw: bytes) -> ExifToolDistributionSpec:
    return ExifToolDistributionSpec(
        candidate_id='test-evidence-win-x64',version='99.1',platform='windows',architecture='x64',
        filename='candidate.zip',source_url='https://example.invalid/candidate.zip',
        size_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),top_level_dir='exiftool-test',
    )


def _good_reports():
    spec = EXIFTOOL_DISTRIBUTIONS['13.55-win-x64']
    exe='a'*64; tree='b'*64
    admission={
        'schema':'gpa.exiftool-distribution-admission.v1','candidate_id':spec.candidate_id,'version':spec.version,
        'platform':spec.platform,'architecture':spec.architecture,'observed_size_bytes':spec.size_bytes,
        'observed_sha256':spec.sha256,'archive_verified':True,'executable_sha256':exe,
        'prepared_distribution_sha256':tree,'checks':{'distribution_prepared':True,'executable_present':True},'errors':[],
    }
    qualification={
        'schema':QUALIFICATION_SCHEMA,'harness':HARNESS_ID,'qualification_id':'qual-123','passed':True,
        'version':spec.version,'executable_sha256':exe,'distribution_sha256':tree,'errors':[],
    }
    return admission,qualification


def _live_fixture(tmp_path):
    raw=_zip_bytes(); spec=_synthetic_spec(raw); archive=tmp_path/'candidate.zip'; archive.write_bytes(raw)
    EXIFTOOL_DISTRIBUTIONS[spec.candidate_id]=spec
    report=admit_distribution(spec,archive,tmp_path/'prepared')
    assert report.archive_verified and not report.errors
    qualification={
        'schema':QUALIFICATION_SCHEMA,'harness':HARNESS_ID,'qualification_id':'qual-live','passed':True,
        'version':spec.version,'executable_sha256':report.executable_sha256,
        'distribution_sha256':report.prepared_distribution_sha256,'errors':[],
    }
    ap=tmp_path/'admission.json'; qp=tmp_path/'qualification.json'
    ap.write_text(json.dumps(report.to_dict(),sort_keys=True)); qp.write_text(json.dumps(qualification,sort_keys=True))
    return spec,archive,Path(report.prepared_distribution_root),Path(report.executable_path),ap,qp


def test_matching_in_memory_identity_is_not_review_ready_without_bound_reports_and_live_artifacts():
    admission,qualification=_good_reports(); b=build_exiftool_evidence_bundle(admission,qualification)
    assert b.schema==EVIDENCE_SCHEMA and not b.ready_for_promotion_review and not b.production_write_approved
    assert b.checks['executable_identity_matches'] and b.checks['distribution_identity_matches']
    assert not b.checks['admission_report_bound'] and not b.checks['live_archive_sha256_matches_pin']


def test_executable_identity_mismatch_blocks_bundle():
    admission,qualification=_good_reports();qualification['executable_sha256']='c'*64
    b=build_exiftool_evidence_bundle(admission,qualification)
    assert not b.ready_for_promotion_review and not b.checks['executable_identity_matches']


def test_distribution_tree_identity_mismatch_blocks_bundle():
    admission,qualification=_good_reports();qualification['distribution_sha256']='c'*64
    b=build_exiftool_evidence_bundle(admission,qualification)
    assert not b.ready_for_promotion_review and not b.checks['distribution_identity_matches']


def test_wrong_version_or_failed_qualification_blocks_bundle():
    admission,qualification=_good_reports();qualification['version']='13.59';qualification['passed']=False
    b=build_exiftool_evidence_bundle(admission,qualification)
    assert not b.ready_for_promotion_review and not b.checks['qualification_version_matches_pin'] and not b.checks['qualification_passed']


def test_report_file_bundle_reverifies_live_archive_executable_and_tree(tmp_path):
    spec,archive,root,exe,ap,qp=_live_fixture(tmp_path)
    try:
        b=bundle_exiftool_report_files(ap,qp,archive_path=archive,distribution_root=root,executable_path=exe)
        assert b.ready_for_promotion_review and not b.production_write_approved
        assert b.checks['live_archive_sha256_matches_pin'] and b.checks['live_executable_identity_matches'] and b.checks['live_distribution_identity_matches']
        assert len(b.admission_report_sha256 or '')==64 and len(b.qualification_report_sha256 or '')==64
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_live_distribution_tamper_is_detected(tmp_path):
    spec,archive,root,exe,ap,qp=_live_fixture(tmp_path)
    try:
        (root/'exiftool_files/lib/x.pm').write_text('tampered')
        b=bundle_exiftool_report_files(ap,qp,archive_path=archive,distribution_root=root,executable_path=exe)
        assert not b.ready_for_promotion_review and not b.checks['live_distribution_identity_matches']
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)


def test_unknown_candidate_fails_closed():
    admission,qualification=_good_reports();admission['candidate_id']='not-pinned'
    b=build_exiftool_evidence_bundle(admission,qualification)
    assert not b.ready_for_promotion_review and b.errors


def test_cli_bundle_writes_live_bound_review_ready_evidence(tmp_path,capsys):
    from gpa.__main__ import main
    spec,archive,root,exe,ap,qp=_live_fixture(tmp_path);out=tmp_path/'bundle.json'
    try:
        rc=main(['bundle-exiftool-evidence','--admission',str(ap),'--qualification',str(qp),'--archive',str(archive),'--distribution-root',str(root),'--executable',str(exe),'--output',str(out)])
        assert rc==0 and out.is_file()
        payload=json.loads(out.read_text());assert payload['ready_for_promotion_review'] is True and payload['production_write_approved'] is False
        assert payload['live_archive_sha256']==spec.sha256
    finally: EXIFTOOL_DISTRIBUTIONS.pop(spec.candidate_id,None)
    capsys.readouterr()
