from __future__ import annotations
import hashlib, json, re, stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from .transactions import sha256_file

_HEX64=re.compile(r'^[0-9a-f]{64}$')

@dataclass(frozen=True)
class AuditProblem:
    kind:str
    path:str
    message:str

@dataclass
class AuditReport:
    assets_checked:int=0
    revisions_checked:int=0
    blobs_checked:int=0
    unclassified_checked:int=0
    identity_observations_checked:int=0
    identity_sightings_checked:int=0
    media_validation_passed:int=0
    media_validation_failed:int=0
    media_validation_unavailable:int=0
    archive_format_status:str='unknown'
    archive_format_id:str|None=None
    archive_format_supported:bool=False
    problems:list[AuditProblem]=field(default_factory=list)

    @property
    def ok(self)->bool:return not self.problems


def _canonical_record_without_revision_id(obj:dict)->bytes:
    record={k:v for k,v in obj.items() if k!='revision_id'}
    return json.dumps(record,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def _safe_under(root:Path,relative:str)->Path|None:
    try:
        p=(root/relative).resolve();r=root.resolve()
        p.relative_to(r);return p
    except (ValueError,OSError):return None




def _checked_exists(path:Path)->tuple[bool|None,OSError|None]:
    """Distinguish genuine absence from storage/stat failure."""
    try:
        path.stat()
        return True,None
    except FileNotFoundError:
        return False,None
    except OSError as e:
        return None,e


def _checked_kind(path:Path, *, directory:bool)->tuple[bool|None,OSError|None]:
    """Check file type without allowing pathlib to suppress stat/device errors."""
    try:
        mode=path.stat().st_mode
    except FileNotFoundError:
        return False,None
    except OSError as e:
        return None,e
    return (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)),None


def audit_archive(root:Path)->AuditReport:
    """Verify the current archive projection and immutable metadata history.

    The audit does not trust SQLite.  It starts from open GPA revision files and
    re-hashes current media/XMP plus immutable revision/blobs.  Old revisions may
    legitimately point at historical projection paths which no longer exist, so
    only the *current* projection is required to exist.
    """
    root=Path(root);out=AuditReport();assets_root=root/'metadata'/'assets';projection_owners={}
    try:
        from .archive_format import inspect_archive_format
        fs=inspect_archive_format(root);out.archive_format_status=fs.status;out.archive_format_id=fs.archive_format;out.archive_format_supported=fs.supported
        if fs.status in {'invalid_manifest','unsupported','foreign_nonempty'}:
            out.problems.append(AuditProblem('bad_archive_format','metadata/archive_format.json',fs.detail))
    except Exception as e:
        out.problems.append(AuditProblem('bad_archive_format','metadata/archive_format.json',str(e)))
    assets_exists,assets_err=_checked_exists(assets_root)
    if assets_exists is None:
        out.problems.append(AuditProblem('unreadable_assets_directory','metadata/assets',str(assets_err)));return out
    if assets_exists is False:
        out.problems.append(AuditProblem('missing_metadata_root','metadata/assets','asset metadata root is missing'));return out

    try:
        asset_entries=sorted(assets_root.iterdir())
    except OSError as e:
        out.problems.append(AuditProblem('unreadable_assets_directory','metadata/assets',str(e)));return out
    asset_dirs=[]
    for entry in asset_entries:
        is_dir,entry_err=_checked_kind(entry,directory=True)
        if is_dir is None:
            out.problems.append(AuditProblem('unreadable_asset_entry',str(entry.relative_to(root)),str(entry_err)));continue
        if is_dir:asset_dirs.append(entry)
    for ad in asset_dirs:
        aid=ad.name;current=ad/'current.json';out.assets_checked+=1
        try:
            current_text=current.read_text(encoding='utf-8')
        except OSError as e:
            out.problems.append(AuditProblem('unreadable_current_pointer',str(current.relative_to(root)),str(e)));continue
        try:
            ptr=json.loads(current_text);rid=ptr['revision_id']
        except Exception as e:
            out.problems.append(AuditProblem('bad_current_pointer',str(current.relative_to(root)),str(e)));continue
        rp=ad/'revisions'/f'{rid}.json'
        rp_exists,rp_err=_checked_exists(rp)
        if rp_exists is None:
            out.problems.append(AuditProblem('unreadable_current_revision',str(rp.relative_to(root)),str(rp_err)));continue
        if rp_exists is False:
            out.problems.append(AuditProblem('missing_current_revision',str(rp.relative_to(root)),'current pointer target is missing'));continue

        # Verify every immutable revision's content-addressed filename.
        try:
            revision_paths=sorted((ad/'revisions').glob('*.json'))
        except OSError as e:
            out.problems.append(AuditProblem('unreadable_revisions_directory',str((ad/'revisions').relative_to(root)),str(e)));continue
        for rev in revision_paths:
            out.revisions_checked+=1
            try:
                revision_text=rev.read_text(encoding='utf-8')
            except OSError as e:
                out.problems.append(AuditProblem('unreadable_revision',str(rev.relative_to(root)),str(e)));continue
            try:
                obj=json.loads(revision_text);declared=obj.get('revision_id')
                expected=hashlib.sha256(_canonical_record_without_revision_id(obj)).hexdigest()[:16]
                if declared!=expected or rev.stem!=expected:
                    out.problems.append(AuditProblem('revision_hash_mismatch',str(rev.relative_to(root)),f'expected revision id {expected}'))
            except Exception as e:
                out.problems.append(AuditProblem('bad_revision',str(rev.relative_to(root)),str(e)))

        try:
            current_revision_text=rp.read_text(encoding='utf-8')
        except OSError as e:
            out.problems.append(AuditProblem('unreadable_current_revision',str(rp.relative_to(root)),str(e)));continue
        try:
            rec=json.loads(current_revision_text)
        except Exception as e:
            out.problems.append(AuditProblem('bad_current_revision',str(rp.relative_to(root)),str(e)));continue
        if rec.get('schema')!='gpa.asset.v1':out.problems.append(AuditProblem('unknown_asset_schema',str(rp.relative_to(root)),str(rec.get('schema'))))
        if rec.get('asset_id')!=aid:out.problems.append(AuditProblem('asset_id_mismatch',str(rp.relative_to(root)),f"record={rec.get('asset_id')} directory={aid}"))
        mv=rec.get('media_validation')
        if isinstance(mv,dict):
            status=mv.get('status')
            if status=='passed':
                out.media_validation_passed+=1
                bad_name=not mv.get('validator')
                bad_version=not mv.get('version') or str(mv.get('version')).strip().lower() in {'unknown','none',''}
                vsha=str(mv.get('validator_sha256') or '')
                msha=str(mv.get('media_sha256') or '')
                if bad_name or bad_version or not _HEX64.match(vsha) or not _HEX64.match(msha) or mv.get('bytes_unchanged') is not True or msha!=rec.get('media_sha256'):
                    out.problems.append(AuditProblem('bad_media_validation_provenance',str(rp.relative_to(root)),'passed validation must record exact validator build SHA-256, exact validated media SHA-256, and bytes_unchanged=true'))
            elif status=='failed':out.media_validation_failed+=1
            elif status=='unavailable':out.media_validation_unavailable+=1
            else:out.problems.append(AuditProblem('bad_media_validation_status',str(rp.relative_to(root)),str(status)))
        else:
            # Legacy records are preserved but cannot claim validated-media status.
            out.media_validation_unavailable+=1

        expected_media_sha=rec.get('media_sha256')
        if not isinstance(expected_media_sha,str) or not _HEX64.match(expected_media_sha):
            out.problems.append(AuditProblem('bad_media_hash',str(rp.relative_to(root)),'missing/invalid media SHA-256'));continue
        proj=rec.get('projection') or {};mrel=proj.get('media')
        if not mrel:
            out.problems.append(AuditProblem('missing_media_projection',str(rp.relative_to(root)),'current revision has no media path'));continue
        media=_safe_under(root,mrel)
        if media is None:
            out.problems.append(AuditProblem('projection_path_escape',str(rp.relative_to(root)),mrel));continue
        owner=projection_owners.setdefault(str(media).casefold(),aid)
        if owner!=aid:out.problems.append(AuditProblem('duplicate_projection',mrel,f'also owned by {owner}'))
        media_exists,media_err=_checked_exists(media)
        if media_exists is None:out.problems.append(AuditProblem('unreadable_media',mrel,str(media_err)))
        elif media_exists is False:out.problems.append(AuditProblem('missing_media',mrel,'current media is missing'))
        else:
            try:
                got=sha256_file(media)
            except OSError as e:
                out.problems.append(AuditProblem('unreadable_media',mrel,str(e)))
            else:
                if got!=expected_media_sha:out.problems.append(AuditProblem('media_hash_mismatch',mrel,f'expected {expected_media_sha}, got {got}'))

        xrel=proj.get('xmp');xsha=proj.get('xmp_sha256')
        if xrel:
            xp=_safe_under(root,xrel)
            if xp is None:out.problems.append(AuditProblem('projection_path_escape',str(rp.relative_to(root)),xrel))
            else:
                xp_exists,xp_err=_checked_exists(xp)
                if xp_exists is None:
                    out.problems.append(AuditProblem('unreadable_xmp',xrel,str(xp_err)));continue
                if xp_exists is False:
                    out.problems.append(AuditProblem('missing_xmp',xrel,'current XMP is missing'));continue
                if xsha:
                    try:xgot=sha256_file(xp)
                    except OSError as e:out.problems.append(AuditProblem('unreadable_xmp',xrel,str(e)))
                    else:
                        if xgot!=xsha:out.problems.append(AuditProblem('xmp_hash_mismatch',xrel,'current XMP hash differs from revision'))
                try:
                    xmp_bytes=xp.read_bytes()
                except OSError as e:
                    out.problems.append(AuditProblem('unreadable_xmp',xrel,str(e)))
                else:
                    try:ET.fromstring(xmp_bytes)
                    except Exception as e:out.problems.append(AuditProblem('invalid_xmp',xrel,str(e)))

        blobs=ad/'blobs'
        blobs_exists,blobs_err=_checked_exists(blobs)
        if blobs_exists is None:
            out.problems.append(AuditProblem('unreadable_blobs_directory',str(blobs.relative_to(root)),str(blobs_err)))
        elif blobs_exists:
            try:
                blob_paths=list(blobs.rglob('*'))
            except OSError as e:
                out.problems.append(AuditProblem('unreadable_blobs_directory',str(blobs.relative_to(root)),str(e)));blob_paths=[]
            for bp in blob_paths:
                is_file,bp_err=_checked_kind(bp,directory=False)
                if is_file is None:
                    out.problems.append(AuditProblem('unreadable_blob_entry',str(bp.relative_to(root)),str(bp_err)));continue
                if not is_file:continue
                out.blobs_checked+=1;stem=bp.stem
                if _HEX64.match(stem):
                    try:bgot=sha256_file(bp)
                    except OSError as e:out.problems.append(AuditProblem('unreadable_blob',str(bp.relative_to(root)),str(e)))
                    else:
                        if bgot!=stem:out.problems.append(AuditProblem('blob_hash_mismatch',str(bp.relative_to(root)),'content does not match content-addressed filename'))

        identity=ad/'identity';valid_observations=set()
        od=identity/'observations'
        od_exists,od_err=_checked_exists(od)
        if od_exists is None:
            out.problems.append(AuditProblem('unreadable_identity_observations_directory',str(od.relative_to(root)),str(od_err)))
        elif od_exists:
            try:
                observation_paths=sorted(od.glob('*.json'))
            except OSError as e:
                out.problems.append(AuditProblem('unreadable_identity_observations_directory',str(od.relative_to(root)),str(e)));observation_paths=[]
            for op in observation_paths:
                out.identity_observations_checked+=1
                try:
                    observation_text=op.read_text(encoding='utf-8')
                except OSError as e:
                    out.problems.append(AuditProblem('unreadable_identity_observation',str(op.relative_to(root)),str(e)));continue
                try:
                    obj=json.loads(observation_text);declared=obj.get('observation_id')
                    payload={k:v for k,v in obj.items() if k!='observation_id'}
                    expected=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()[:24]
                    if obj.get('schema')!='gpa.identity-observation.v1':
                        out.problems.append(AuditProblem('unknown_identity_observation_schema',str(op.relative_to(root)),str(obj.get('schema'))))
                    if declared!=expected or op.stem!=expected:
                        out.problems.append(AuditProblem('identity_observation_hash_mismatch',str(op.relative_to(root)),f'expected observation id {expected}'))
                    else:valid_observations.add(expected)
                    if obj.get('content_sha256')!=expected_media_sha:
                        out.problems.append(AuditProblem('identity_observation_content_mismatch',str(op.relative_to(root)),'observation content SHA-256 differs from asset content'))
                except Exception as e:
                    out.problems.append(AuditProblem('bad_identity_observation',str(op.relative_to(root)),str(e)))
        sd=identity/'sightings'
        sd_exists,sd_err=_checked_exists(sd)
        if sd_exists is None:
            out.problems.append(AuditProblem('unreadable_identity_sightings_directory',str(sd.relative_to(root)),str(sd_err)))
        elif sd_exists:
            try:
                sighting_paths=sorted(sd.glob('*.json'))
            except OSError as e:
                out.problems.append(AuditProblem('unreadable_identity_sightings_directory',str(sd.relative_to(root)),str(e)));sighting_paths=[]
            for sp in sighting_paths:
                out.identity_sightings_checked+=1
                try:
                    sighting_text=sp.read_text(encoding='utf-8')
                except OSError as e:
                    out.problems.append(AuditProblem('unreadable_identity_sighting',str(sp.relative_to(root)),str(e)));continue
                try:
                    obj=json.loads(sighting_text);declared=obj.get('sighting_id')
                    payload={k:v for k,v in obj.items() if k!='sighting_id'}
                    expected=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()[:24]
                    if obj.get('schema')!='gpa.identity-sighting.v1':
                        out.problems.append(AuditProblem('unknown_identity_sighting_schema',str(sp.relative_to(root)),str(obj.get('schema'))))
                    if declared!=expected or sp.stem!=expected:
                        out.problems.append(AuditProblem('identity_sighting_hash_mismatch',str(sp.relative_to(root)),f'expected sighting id {expected}'))
                    oid=obj.get('observation_id');rid_seen=obj.get('revision_id')
                    if oid not in valid_observations:
                        out.problems.append(AuditProblem('identity_sighting_missing_observation',str(sp.relative_to(root)),str(oid)))
                    seen_revision=ad/'revisions'/f'{rid_seen}.json';seen_exists,seen_err=_checked_exists(seen_revision)
                    if seen_exists is None:
                        out.problems.append(AuditProblem('unreadable_identity_sighting_revision',str(sp.relative_to(root)),str(seen_err)))
                    elif seen_exists is False:
                        out.problems.append(AuditProblem('identity_sighting_missing_revision',str(sp.relative_to(root)),str(rid_seen)))
                except Exception as e:
                    out.problems.append(AuditProblem('bad_identity_sighting',str(sp.relative_to(root)),str(e)))

    # Verify preserved unknown/unclassified material using durable run manifests.
    runs=root/'metadata'/'runs'
    runs_exists,runs_err=_checked_exists(runs)
    if runs_exists is None:
        out.problems.append(AuditProblem('unreadable_runs_directory',str(runs.relative_to(root)),str(runs_err)))
    elif runs_exists:
        seen=set()
        try:
            run_paths=sorted(runs.glob('*.json'))
        except OSError as e:
            out.problems.append(AuditProblem('unreadable_runs_directory',str(runs.relative_to(root)),str(e)));run_paths=[]
        for mp in run_paths:
            try:
                manifest_text=mp.read_text(encoding='utf-8')
            except OSError as e:
                out.problems.append(AuditProblem('unreadable_run_manifest',str(mp.relative_to(root)),str(e)));continue
            try:m=json.loads(manifest_text)
            except Exception as e:
                out.problems.append(AuditProblem('bad_run_manifest',str(mp.relative_to(root)),str(e)));continue
            if not isinstance(m,dict):
                out.problems.append(AuditProblem('bad_run_manifest',str(mp.relative_to(root)),'run manifest must be a JSON object'));continue
            rows=m.get('unclassified_preserved',[])
            if not isinstance(rows,list):
                out.problems.append(AuditProblem('bad_run_manifest',str(mp.relative_to(root)),'unclassified_preserved must be a JSON array'));continue
            for index,row in enumerate(rows):
                if not isinstance(row,dict):
                    out.problems.append(AuditProblem('bad_unclassified_manifest_entry',str(mp.relative_to(root)),f'unclassified_preserved[{index}] must be a JSON object'));continue
                key=(row.get('saved_as'),row.get('sha256'))
                if key in seen:continue
                seen.add(key);out.unclassified_checked+=1
                rel,digest=key
                p=_safe_under(root,rel) if rel else None
                if p is None:
                    out.problems.append(AuditProblem('missing_unclassified',str(rel),'preserved unknown material is missing'))
                else:
                    p_exists,p_err=_checked_exists(p)
                    if p_exists is None:
                        out.problems.append(AuditProblem('unreadable_unclassified',str(rel),str(p_err)));continue
                    if p_exists is False:
                        out.problems.append(AuditProblem('missing_unclassified',str(rel),'preserved unknown material is missing'));continue
                if digest:
                    try:ugot=sha256_file(p)
                    except OSError as e:out.problems.append(AuditProblem('unreadable_unclassified',str(rel),str(e)))
                    else:
                        if ugot!=digest:out.problems.append(AuditProblem('unclassified_hash_mismatch',str(rel),'preserved unknown material changed'))
    return out
