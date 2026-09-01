from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path

from .identity import _material_signature
from .planner import LogicalAsset
from .transactions import write_json_new


OBS_SCHEMA = 'gpa.identity-observation.v1'
SIGHTING_SCHEMA = 'gpa.identity-sighting.v1'


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _short_id(obj: dict) -> str:
    return hashlib.sha256(_canonical(obj)).hexdigest()[:24]


@dataclass(frozen=True)
class IdentityObservation:
    content_sha256: str
    url_hint: str | None
    material_signature: str

    def payload(self) -> dict:
        return {
            'schema': OBS_SCHEMA,
            'content_sha256': self.content_sha256,
            'url_hint': self.url_hint,
            'material_signature': self.material_signature,
        }

    @property
    def observation_id(self) -> str:
        return _short_id(self.payload())


@dataclass(frozen=True)
class IdentitySighting:
    observation_id: str
    revision_id: str

    def payload(self) -> dict:
        return {
            'schema': SIGHTING_SCHEMA,
            'observation_id': self.observation_id,
            'revision_id': self.revision_id,
        }

    @property
    def sighting_id(self) -> str:
        return _short_id(self.payload())


@dataclass
class PersistentIdentitySummary:
    schema: str = 'gpa.identity-summary.v1'
    observation_count: int = 0
    sighting_count: int = 0
    distinct_url_hints: list[str] = field(default_factory=list)
    material_signatures: list[str] = field(default_factory=list)
    possible_logical_item_count: int = 1
    multiplicity_confidence: str = 'unknown'
    needs_identity_review: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class IdentityObservationStore:
    """Immutable cross-revision logical-item evidence for a content object.

    Media SHA-256 remains the content identity.  Google Photos URL values are only
    undocumented hints, so this store never promotes them to authoritative IDs.
    Observations capture identity-relevant metadata signatures; sightings link those
    observations to immutable GPA revisions where full raw/provenance data lives.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _base(self, asset_id: str) -> Path:
        return self.root / 'metadata' / 'assets' / asset_id / 'identity'

    def observations_dir(self, asset_id: str) -> Path:
        return self._base(asset_id) / 'observations'

    def sightings_dir(self, asset_id: str) -> Path:
        return self._base(asset_id) / 'sightings'


    def _persist_observation(self, asset_id: str, content_sha256: str, url_hint: str | None, material_signature: str, revision_id: str) -> str:
        obs = IdentityObservation(content_sha256, url_hint or None, material_signature)
        op = self.observations_dir(asset_id) / f'{obs.observation_id}.json'
        if not op.exists():
            write_json_new(op, {'observation_id': obs.observation_id, **obs.payload()})
        sight = IdentitySighting(obs.observation_id, revision_id)
        sp = self.sightings_dir(asset_id) / f'{sight.sighting_id}.json'
        if not sp.exists():
            write_json_new(sp, {'sighting_id': sight.sighting_id, **sight.payload()})
        return obs.observation_id

    def observe(self, asset: LogicalAsset, revision_id: str) -> list[str]:
        """Persist identity observations represented by one imported asset state.

        Repeated album/year manifestations with the same URL hint and material
        signature deduplicate to one observation.  A later corrected metadata state
        using the same URL creates another observation but still aggregates as one
        probable logical item; different URL hints remain separate multiplicity evidence.
        """
        ids: list[str] = []
        for sc in asset.sidecar_values:
            ids.append(self._persist_observation(asset.asset_id, asset.sha256, sc.url or None, _material_signature(sc), revision_id))
        return sorted(set(ids))

    def observe_revision_record(self, asset_id: str, record: dict, revision_id: str) -> list[str]:
        """Backfill observations from an immutable GPA revision record.

        Full raw Google metadata remains in the revision.  This derives only the
        identity-relevant observation key, so the operation is repeatable and does
        not change the revision or current projection.
        """
        from .sidecars import parse_google_sidecar
        content_sha256 = record.get('media_sha256')
        if not isinstance(content_sha256, str):
            return []
        ids: list[str] = []
        for raw in record.get('google_raw') or []:
            if not isinstance(raw, dict):
                continue
            sc = parse_google_sidecar(raw)
            ids.append(self._persist_observation(asset_id, content_sha256, sc.url or None, _material_signature(sc), revision_id))
        return sorted(set(ids))

    def observations(self, asset_id: str) -> list[dict]:
        out = []
        d = self.observations_dir(asset_id)
        if not d.exists():
            return out
        for p in sorted(d.glob('*.json')):
            try:
                obj = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            out.append(obj)
        return out

    def sightings(self, asset_id: str) -> list[dict]:
        out = []
        d = self.sightings_dir(asset_id)
        if not d.exists():
            return out
        for p in sorted(d.glob('*.json')):
            try:
                obj = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            out.append(obj)
        return out

    @staticmethod
    def _summarize(obs: list[dict], sighting_count: int = 0) -> PersistentIdentitySummary:
        out = PersistentIdentitySummary(observation_count=len(obs), sighting_count=sighting_count)
        if not obs:
            out.notes.append('No persistent Google logical-item observations have been recorded for this content object.')
            return out

        urls = sorted({str(o.get('url_hint')) for o in obs if o.get('url_hint')})
        sigs = sorted({str(o.get('material_signature')) for o in obs if o.get('material_signature')})
        out.distinct_url_hints = urls
        out.material_signatures = sigs

        by_url: dict[str, set[str]] = {}
        no_url_sigs: set[str] = set()
        for o in obs:
            sig = str(o.get('material_signature') or '')
            url = o.get('url_hint')
            if url:
                by_url.setdefault(str(url), set()).add(sig)
            elif sig:
                no_url_sigs.add(sig)

        linked_sigs = set().union(*by_url.values()) if by_url else set()
        unlinked_no_url = no_url_sigs - linked_sigs
        if by_url:
            possible = len(by_url) + len(unlinked_no_url)
        else:
            possible = max(1, len(no_url_sigs))
        out.possible_logical_item_count = max(1, possible)

        if len(by_url) > 1:
            out.multiplicity_confidence = 'probable'
            out.needs_identity_review = True
            out.notes.append('Identical media content has multiple Google Photos URL hints across imports; URLs are preserved as probable logical-item evidence, not authoritative IDs.')
        elif unlinked_no_url and by_url:
            out.multiplicity_confidence = 'possible'
            out.needs_identity_review = True
            out.notes.append('Some identity observations lack URL hints and have metadata signatures not linked to the URL-backed observation; multiplicity remains possible.')
        elif not by_url and len(no_url_sigs) > 1:
            out.multiplicity_confidence = 'possible'
            out.needs_identity_review = True
            out.notes.append('Identical media content has materially different Google metadata observations without stable item identifiers; preserve the disagreement for review.')
        elif len(by_url) == 1:
            out.multiplicity_confidence = 'probable'
            if len(next(iter(by_url.values()))) > 1:
                out.notes.append('One Google Photos URL hint has multiple material metadata observations, consistent with one probable item whose Google-side metadata evolved over time.')
            else:
                out.notes.append('One Google Photos URL hint is consistently observed; this supports, but does not prove, one logical Google item.')
        else:
            out.notes.append('No Google Photos URL hint is available; SHA-256 proves content identity only.')
        return out

    def summary(self, asset_id: str) -> PersistentIdentitySummary:
        return self._summarize(self.observations(asset_id), len(self.sightings(asset_id)))

    def preview_summary(self, asset: LogicalAsset) -> PersistentIdentitySummary:
        """Combine stored observations with incoming asset evidence without writing."""
        obs = list(self.observations(asset.asset_id))
        known = {(o.get('content_sha256'), o.get('url_hint'), o.get('material_signature')) for o in obs}
        for sc in asset.sidecar_values:
            row = {
                'schema': OBS_SCHEMA,
                'content_sha256': asset.sha256,
                'url_hint': sc.url or None,
                'material_signature': _material_signature(sc),
            }
            key = (row['content_sha256'], row['url_hint'], row['material_signature'])
            if key not in known:
                obs.append(row); known.add(key)
        return self._summarize(obs, len(self.sightings(asset.asset_id)))


def backfill_identity_observations(root: Path) -> dict:
    """Populate the persistent identity ledger from all existing immutable revisions.

    This is an idempotent metadata-only upgrade path for archives created before
    identity observations existed.  It never touches current media/XMP projection.
    """
    root = Path(root); store = IdentityObservationStore(root); assets_root = root / 'metadata' / 'assets'
    result = {'schema': 'gpa.identity-backfill.v1', 'assets_seen': 0, 'revisions_seen': 0, 'observations_written_or_found': 0, 'problems': []}
    if not assets_root.exists():
        return result
    for ad in sorted(p for p in assets_root.iterdir() if p.is_dir()):
        result['assets_seen'] += 1
        for rp in sorted((ad / 'revisions').glob('*.json')):
            result['revisions_seen'] += 1
            try:
                obj = json.loads(rp.read_text(encoding='utf-8'))
                rid = str(obj.get('revision_id') or rp.stem)
                result['observations_written_or_found'] += len(store.observe_revision_record(ad.name, obj, rid))
            except Exception as e:
                result['problems'].append({'asset_id': ad.name, 'revision': rp.name, 'error': str(e)})
    return result
