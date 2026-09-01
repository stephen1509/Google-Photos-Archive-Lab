from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from gpa.audit import audit_archive
from gpa.catalog import rebuild_catalog_from_archive
from gpa.executor import RunExecutor
from gpa.identity_observations import IdentityObservationStore
from gpa.revisions import RevisionStore


def _mkzip(path: Path, entries: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, value in entries.items():
            z.writestr(name, value.encode('utf-8') if isinstance(value, str) else value)


def _sidecar(title='a.jpg', *, url=None, ts='1494806400', description='') -> str:
    obj = {'title': title, 'photoTakenTime': {'timestamp': ts}, 'description': description}
    if url:
        obj['url'] = url
    return json.dumps(obj)


def _run(z: Path, root: Path, run_id: str) -> str:
    ex = RunExecutor([z], root, embedded_provider=lambda m, l: None, storage_reserve_fraction=0)
    manifest = ex.execute(run_id=run_id)
    assert manifest['status'] == 'complete'
    assert len(manifest['successes']) == 1
    return manifest['successes'][0]['asset_id']


def test_cross_run_same_bytes_different_google_urls_preserve_two_identity_observations(tmp_path):
    root = tmp_path / 'archive'; z1 = tmp_path / 'one.zip'; z2 = tmp_path / 'two.zip'
    _mkzip(z1, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/ONE')})
    _mkzip(z2, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/TWO')})
    aid = _run(z1, root, 'one'); assert _run(z2, root, 'two') == aid

    store = IdentityObservationStore(root); summary = store.summary(aid)
    assert summary.observation_count == 2
    assert summary.possible_logical_item_count == 2
    assert summary.needs_identity_review
    assert summary.multiplicity_confidence == 'probable'
    assert summary.distinct_url_hints == ['https://photos.google.com/photo/ONE', 'https://photos.google.com/photo/TWO']
    assert len(RevisionStore(root).revisions(aid)) == 2
    # Content bytes exist only once in the current projection despite two logical-item hints.
    assert len(list((root / 'Photos').rglob('*.jpg'))) == 1


def test_same_google_url_with_corrected_metadata_is_one_probable_item_with_history(tmp_path):
    root = tmp_path / 'archive'; z1 = tmp_path / 'one.zip'; z2 = tmp_path / 'two.zip'; url = 'https://photos.google.com/photo/ONE'
    _mkzip(z1, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url=url, description='old')})
    _mkzip(z2, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url=url, description='corrected')})
    aid = _run(z1, root, 'one'); _run(z2, root, 'two')
    summary = IdentityObservationStore(root).summary(aid)
    assert summary.observation_count == 2
    assert summary.possible_logical_item_count == 1
    assert not summary.needs_identity_review
    assert summary.distinct_url_hints == [url]
    assert any('metadata evolved' in n for n in summary.notes)


def test_repeated_album_year_manifestation_deduplicates_identity_observation(tmp_path):
    root = tmp_path / 'archive'; z1 = tmp_path / 'one.zip'; z2 = tmp_path / 'two.zip'; url = 'https://photos.google.com/photo/ONE'
    payload = _sidecar(url=url)
    _mkzip(z1, {'Year/a.jpg': b'SAME', 'Year/a.jpg.json': payload})
    _mkzip(z2, {'Album/a.jpg': b'SAME', 'Album/a.jpg.json': payload})
    aid = _run(z1, root, 'one'); _run(z2, root, 'two')
    store = IdentityObservationStore(root); summary = store.summary(aid)
    assert summary.observation_count == 1
    assert summary.sighting_count == 2
    assert summary.possible_logical_item_count == 1


def test_cross_run_no_url_material_disagreement_remains_possible_multiplicity(tmp_path):
    root = tmp_path / 'archive'; z1 = tmp_path / 'one.zip'; z2 = tmp_path / 'two.zip'
    _mkzip(z1, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(description='one')})
    _mkzip(z2, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(description='two')})
    aid = _run(z1, root, 'one'); _run(z2, root, 'two')
    summary = IdentityObservationStore(root).summary(aid)
    assert summary.possible_logical_item_count == 2
    assert summary.multiplicity_confidence == 'possible'
    assert summary.needs_identity_review


def test_catalog_rebuild_surfaces_persistent_identity_review_without_using_stale_metadata(tmp_path):
    root = tmp_path / 'archive'; z1 = tmp_path / 'one.zip'; z2 = tmp_path / 'two.zip'
    _mkzip(z1, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/ONE', description='old')})
    _mkzip(z2, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/TWO', description='current')})
    aid = _run(z1, root, 'one'); _run(z2, root, 'two')
    c = rebuild_catalog_from_archive(root, root / 'metadata' / 'catalog.sqlite')
    row = c.db.execute('SELECT description,identity_review,logical_item_count FROM assets WHERE id=?', (aid,)).fetchone()
    assert row == ('current', 1, 2)
    assert c.identity_review() == [(aid, c.db.execute('SELECT path FROM assets WHERE id=?',(aid,)).fetchone()[0])]
    c.close()


def test_audit_verifies_identity_observation_hash_and_sighting_links(tmp_path):
    root = tmp_path / 'archive'; z = tmp_path / 'one.zip'
    _mkzip(z, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/ONE')})
    aid = _run(z, root, 'one')
    good = audit_archive(root)
    assert good.ok and good.identity_observations_checked == 1 and good.identity_sightings_checked == 1

    op = next((root / 'metadata' / 'assets' / aid / 'identity' / 'observations').glob('*.json'))
    obj = json.loads(op.read_text()); obj['url_hint'] = 'tampered'; op.write_text(json.dumps(obj))
    bad = audit_archive(root)
    assert not bad.ok
    assert any(p.kind == 'identity_observation_hash_mismatch' for p in bad.problems)


def test_backfill_identity_observations_upgrades_legacy_revisions_idempotently(tmp_path):
    from gpa.identity_observations import backfill_identity_observations
    root = tmp_path / 'archive'; store = RevisionStore(root); aid = hashlib.sha256(b'SAME').hexdigest()[:24]
    raw = json.loads(_sidecar(url='https://photos.google.com/photo/LEGACY', description='legacy'))
    record = {
        'schema': 'gpa.asset.v1', 'asset_id': aid, 'media_sha256': hashlib.sha256(b'SAME').hexdigest(),
        'projection': {'media': 'Photos/2017/05 - May/a.jpg', 'xmp': None, 'xmp_sha256': None},
        'date': {'precision':'month','confidence':'proven','value':None,'year':2017,'month':5,'start':None,'end':None,'timezone_known':False,'source':'legacy','note':None},
        'google_raw': [raw],
    }
    rp = store.add_revision(aid, record)
    assert IdentityObservationStore(root).summary(aid).observation_count == 0
    first = backfill_identity_observations(root); second = backfill_identity_observations(root)
    summary = IdentityObservationStore(root).summary(aid)
    assert not first['problems'] and not second['problems']
    assert summary.observation_count == 1 and summary.sighting_count == 1
    assert summary.distinct_url_hints == ['https://photos.google.com/photo/LEGACY']


def test_cli_backfill_identity_command(tmp_path, capsys):
    from gpa.__main__ import main
    root = tmp_path / 'archive'; store = RevisionStore(root); aid = 'a' * 24
    record = {'schema':'gpa.asset.v1','asset_id':aid,'media_sha256':'0'*64,'projection':{'media':'Photos/x.jpg','xmp':None,'xmp_sha256':None},'google_raw':[json.loads(_sidecar(url='u'))]}
    store.add_revision(aid, record)
    assert main(['backfill-identity','--output',str(root)]) == 0
    assert 'identity-backfill' in capsys.readouterr().out
    assert IdentityObservationStore(root).summary(aid).observation_count == 1


def test_preview_detects_cross_run_identity_multiplicity_before_writing(tmp_path):
    from gpa.preview import build_run_preview
    root = tmp_path / 'archive'; z1 = tmp_path / 'one.zip'; z2 = tmp_path / 'two.zip'
    _mkzip(z1, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/ONE')})
    _mkzip(z2, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/TWO')})
    _run(z1, root, 'one')
    ex = RunExecutor([z2], root, embedded_provider=lambda m,l:None, storage_reserve_fraction=0)
    report = ex.plan(); preview = build_run_preview(ex, report)
    assert preview.identity_review_items == 1
    assert any('logical Google Photos item' in n for n in preview.notes)
    # Preview must remain read-only: second observation is not persisted yet.
    aid = report.assets[0].asset_id
    assert IdentityObservationStore(root).summary(aid).observation_count == 1


def test_verification_reports_persistent_identity_review_without_claiming_metadata_complete(tmp_path):
    from gpa.verification import verify_migration
    root = tmp_path / 'archive'; z1 = tmp_path / 'one.zip'; z2 = tmp_path / 'two.zip'
    _mkzip(z1, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/ONE')})
    _mkzip(z2, {'D/a.jpg': b'SAME', 'D/a.jpg.json': _sidecar(url='https://photos.google.com/photo/TWO')})
    _run(z1, root, 'one'); _run(z2, root, 'two')
    v = verify_migration(root, require_source_vault=False, require_source_vault_redundancy=False, require_takeout_receipt=False, require_exit_evidence=False, require_independent_media=False)
    assert v.identity_review_assets == 1
    assert not v.organization_complete
    assert any('identity multiplicity' in n for n in v.notes)


def test_portable_xmp_keeps_agreed_description_despite_nonportable_json_differences(tmp_path):
    from gpa.materializer import _consensus_sidecar
    from gpa.sidecars import parse_google_sidecar
    from gpa.model import DateFact, DatePrecision, Confidence
    from gpa.writepolicy import plan_metadata
    from gpa.xmp import render_xmp_sidecar
    a=parse_google_sidecar(json.loads(_sidecar(url='URL-A',description='Nara trip')))
    b=parse_google_sidecar(json.loads(_sidecar(url='URL-B',description='Nara trip')))
    c=_consensus_sidecar([a,b]);assert c and c.description=='Nara trip' and c.url is None
    f=DateFact(DatePrecision.MONTH,Confidence.PROVEN,year=2017,month=5,source='test')
    x=render_xmp_sidecar(plan_metadata(f,c));assert x and b'Nara trip' in x


def test_portable_xmp_omits_disputed_description_for_identical_bytes(tmp_path):
    from gpa.materializer import _consensus_sidecar
    from gpa.sidecars import parse_google_sidecar
    from gpa.model import DateFact, DatePrecision, Confidence
    from gpa.writepolicy import plan_metadata
    from gpa.xmp import render_xmp_sidecar
    a=parse_google_sidecar(json.loads(_sidecar(url='URL-A',description='one')))
    b=parse_google_sidecar(json.loads(_sidecar(url='URL-B',description='two')))
    c=_consensus_sidecar([a,b]);assert c and c.description is None
    f=DateFact(DatePrecision.MONTH,Confidence.PROVEN,year=2017,month=5,source='test')
    x=render_xmp_sidecar(plan_metadata(f,c));assert x and b'<dc:description' not in x


def test_cli_audit_reports_identity_ledger_counts(tmp_path,capsys):
    from gpa.__main__ import main
    root=tmp_path/'archive';z=tmp_path/'one.zip'
    _mkzip(z,{'D/a.jpg':b'SAME','D/a.jpg.json':_sidecar(url='u')});_run(z,root,'one')
    assert main(['audit','--output',str(root)])==0
    obj=json.loads(capsys.readouterr().out)
    assert obj['identity_observations_checked']==1 and obj['identity_sightings_checked']==1
