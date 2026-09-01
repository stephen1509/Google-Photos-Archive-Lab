from __future__ import annotations
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

from .review_queue import build_review_queue

@dataclass
class RunPreview:
    schema:str='gpa.preview.v1'
    status:str='ready'
    can_execute:bool=True
    assets_planned:int=0
    actions:dict[str,int]=field(default_factory=dict)
    blocking_review_items:int=0
    optional_review_items:int=0
    identity_review_items:int=0
    source_problems:int=0
    unclassified_items:int=0
    takeout_sequence_known_gaps:int=0
    source_understanding_complete:bool=False
    required_free_bytes:int=0
    permanent_growth_bytes:int=0
    transient_peak_bytes:int=0
    free_bytes:int|None=None
    storage_errors:list[str]=field(default_factory=list)
    storage_warnings:list[str]=field(default_factory=list)
    archive_format_status:str='unknown'
    archive_format_id:str|None=None
    notes:list[str]=field(default_factory=list)

    def to_dict(self)->dict:return asdict(self)


def build_run_preview(executor,report=None)->RunPreview:
    """Build a strictly read-only pre-execution summary."""
    report=report or executor.plan();storage=executor.storage_preflight(report);queue=build_review_queue(report.assets)
    from .archive_format import inspect_archive_format
    archive_state=inspect_archive_format(executor.root)
    from .takeout_parts import analyze_takeout_parts
    part_analysis=analyze_takeout_parts(executor.archives);known_gaps=part_analysis.known_gaps
    blocking=sum(1 for x in queue if x.blocks_placement)
    optional=sum(1 for x in queue if not x.blocks_placement and x.optional)
    from .identity_observations import IdentityObservationStore
    identity_store=IdentityObservationStore(executor.root)
    identity_review=sum(1 for a in report.assets if identity_store.preview_summary(a).needs_identity_review)
    archive_ok=archive_state.can_execute_without_upgrade
    if not storage.ok or not archive_ok:status='blocked'
    elif report.source_problems or known_gaps:status='partial_source'
    elif report.unclassified or blocking:status='needs_review'
    else:status='ready'
    notes=[]
    if not archive_ok:notes.append(f'Archive format blocks execution: {archive_state.status}: {archive_state.detail}')
    if optional:notes.append(f'{optional} asset(s) have optional metadata gaps that do not block folder placement.')
    if identity_review:notes.append(f'{identity_review} content object(s) have possible multiple logical Google Photos item identities requiring review; media bytes and all identity observations remain preserved.')
    if report.unclassified:notes.append('Unclassified Takeout material will be preserved byte-for-byte but requires review.')
    if report.source_problems:notes.append('Healthy source material may still be processed, but the migration cannot be declared complete until source problems are resolved.')
    if known_gaps:notes.append(f'Takeout archive filenames reveal {known_gaps} known missing internal part(s).')
    return RunPreview(
        status=status,can_execute=storage.ok and archive_ok,assets_planned=len(report.assets),actions=dict(storage.operations),
        blocking_review_items=blocking,optional_review_items=optional,identity_review_items=identity_review,source_problems=len(report.source_problems),
        unclassified_items=len(report.unclassified),takeout_sequence_known_gaps=known_gaps,source_understanding_complete=report.complete_source_understanding and not known_gaps,
        required_free_bytes=storage.required_bytes,permanent_growth_bytes=storage.permanent_growth_bytes,
        transient_peak_bytes=storage.transient_peak_bytes,free_bytes=storage.free_bytes,
        storage_errors=list(storage.errors),storage_warnings=list(storage.warnings),archive_format_status=archive_state.status,archive_format_id=archive_state.archive_format,notes=notes,
    )


def write_run_preview(path:Path,preview:RunPreview)->Path:
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(preview.to_dict(),ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(path);return path
