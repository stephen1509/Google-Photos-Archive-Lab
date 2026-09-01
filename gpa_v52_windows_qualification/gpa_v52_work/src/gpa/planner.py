from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import os
from .transactions import sha256_file
from .zipindex import ZipLibrary, Member, SourceProblem
from .sidecars import MEDIA_SUFFIXES, _is_album_metadata_object
from .indexed_sidecars import SidecarIndex
from .chronology import resolve_google_time
from .model import DateFact, DatePrecision, Confidence, SourceRef, GoogleSidecar
from .output import placement_dir, portable_filename, resolve_filename_collisions
from .albums import AlbumIndex, AlbumRecord
from .location import LocationFact, LocationPolicy, resolve_location
from .embedded import EmbeddedMetadata, reconcile_google_and_camera, CompositeZipEmbeddedProvider
from .gazetteer import PlaceEvidence
from .timezones import TimezoneEvidence, resolve_with_timezone_evidence
from .families import EmbeddedEvidence, match_live_photos, apply_live_photo_placement, variant_hints
from .identity import ContentIdentityEvidence, analyze_content_identity

@dataclass
class LogicalAsset:
    asset_id:str
    sha256:str
    occurrences:list[SourceRef]=field(default_factory=list)
    sidecars:list[SourceRef]=field(default_factory=list)
    sidecar_values:list[GoogleSidecar]=field(default_factory=list)
    date_fact:DateFact|None=None
    warnings:list[str]=field(default_factory=list)
    albums:list[AlbumRecord]=field(default_factory=list)
    location_fact:LocationFact|None=None
    placement_fact:DateFact|None=None
    family_relations:list[dict]=field(default_factory=list)
    output_dir:str|None=None
    output_name:str|None=None
    embedded:EmbeddedMetadata|None=None
    place_evidence:PlaceEvidence|None=None
    timezone_evidence:TimezoneEvidence|None=None
    timezone_suggestion:str|None=None
    user_location_label:str|None=None
    user_decisions:list[dict]=field(default_factory=list)
    content_identity:ContentIdentityEvidence|None=None
    occurrence_contexts:list[dict]=field(default_factory=list)

@dataclass
class PlanningReport:
    assets:list[LogicalAsset]
    source_problems:list[SourceProblem]=field(default_factory=list)
    unclassified:list[SourceRef]=field(default_factory=list)
    archive_stats:dict[str,tuple[int,int,str]]=field(default_factory=dict)

    @property
    def complete_source_understanding(self)->bool:
        return not self.source_problems and not self.unclassified

class LibraryPlanner:
    def __init__(self,archives,location_policy:LocationPolicy=LocationPolicy.PREFER_GOOGLE_CURRENT,embedded_provider=None,place_resolver=None,timezone_resolver=None):
        self.lib=ZipLibrary(archives);self.location_policy=location_policy;self.embedded_provider=embedded_provider if embedded_provider is not None else CompositeZipEmbeddedProvider();self.place_resolver=place_resolver;self.timezone_resolver=timezone_resolver

    def _build(self,members:list[Member],hashes:dict[tuple[str,str],str],sidx:SidecarIndex)->tuple[list[LogicalAsset],set[tuple[str,str]],set[tuple[str,str]]]:
        media=[m for m in members if m.suffix in MEDIA_SUFFIXES and (m.archive,m.path) in hashes]
        aidx=AlbumIndex(members,sidx.parsed);groups={};matched_sidecars=set();album_json=set()
        for album in aidx.by_dir.values():
            for r in album.sources:album_json.add((r.archive,r.path))
        for m in media:
            h=hashes[(m.archive,m.path)];a=groups.setdefault(h,LogicalAsset(h[:24],h))
            a.occurrences.append(m.ref)
            album=aidx.album_for(m)
            if album and all(x.album_id!=album.album_id for x in a.albums):a.albums.append(album)
            got=sidx.match(m)
            matched_for_occurrence=[]
            if got:
                sms,sc=got
                for sm in sms:
                    key=(sm.archive,sm.path);matched_sidecars.add(key)
                    matched_for_occurrence.append({'archive':sm.archive,'path':sm.path})
                    if all((r.archive,r.path)!=key for r in a.sidecars):a.sidecars.append(sm.ref)
                a.sidecar_values.append(sc)
            a.occurrence_contexts.append({
                'archive':m.archive,'path':m.path,
                'album_id':album.album_id if album else None,
                'album_title':album.title if album else None,
                'google_sidecars':matched_for_occurrence,
            })
        member_by_ref={(m.archive,m.path):m for m in media}
        selected=[]
        for a in groups.values():
            if a.occurrences:
                first=a.occurrences[0];m=member_by_ref.get((first.archive,first.path))
                if m:selected.append(m)
        embedded_by_ref={}
        if self.embedded_provider and selected and hasattr(self.embedded_provider,'batch'):
            try:embedded_by_ref=self.embedded_provider.batch(selected,self.lib) or {}
            except Exception:
                # Batch failure must not lose the library.  Fall back to isolated
                # per-asset inspection so one bad member cannot poison all evidence.
                embedded_by_ref={}
        for a in groups.values():
            a.content_identity=analyze_content_identity(a.sha256,a.sidecar_values)
            if a.content_identity.needs_identity_review:
                a.warnings.append(f'exact media bytes may represent {a.content_identity.possible_logical_item_count} logical Google Photos items; identity evidence preserved for review')
            if self.embedded_provider and a.occurrences:
                first=a.occurrences[0];m=member_by_ref.get((first.archive,first.path))
                if m:
                    key=(m.archive,m.path)
                    if key in embedded_by_ref:
                        a.embedded=embedded_by_ref[key]
                    else:
                        try:a.embedded=self.embedded_provider(m,self.lib)
                        except Exception as e:a.warnings.append(f'embedded metadata inspection failed: {e}')
            a.date_fact=reconcile_google_and_camera(a.sidecar_values,a.embedded)
            if a.date_fact.confidence==Confidence.CONFLICT:a.warnings.append('chronology conflict across Google/embedded evidence')
            embedded_gps=a.embedded.gps if a.embedded else None
            a.location_fact=resolve_location(a.sidecar_values,self.location_policy,embedded_gps)
            a.warnings.extend(a.location_fact.warnings)
            # GPS may prove the historical timezone for Google's UTC capture instant.
            # Only a historically authoritative boundary result may upgrade chronology;
            # probable timezone evidence is retained as a suggestion, never as fact.
            epochs={x.taken_epoch for x in a.sidecar_values if x.taken_epoch is not None}
            if self.timezone_resolver and a.location_fact.exact and len(epochs)==1 and a.date_fact.confidence!=Confidence.CONFLICT:
                epoch=next(iter(epochs))
                try:
                    from datetime import datetime, timezone
                    capture_year=datetime.fromtimestamp(epoch,timezone.utc).year
                    ev=self.timezone_resolver.evidence_at(a.location_fact.lat,a.location_fact.lon,capture_year)
                    a.timezone_evidence=ev
                    representative=next(x for x in a.sidecar_values if x.taken_epoch==epoch)
                    tzfact,suggestion=resolve_with_timezone_evidence(representative,ev)
                    if suggestion is not None:a.timezone_suggestion=suggestion.isoformat()
                    if ev.confidence==Confidence.PROVEN and tzfact.precision==DatePrecision.INSTANT:
                        # Do not override an independently proven exact camera/Google agreement
                        # unless the month agrees.  Disagreement becomes review, not replacement.
                        if a.date_fact.precision==DatePrecision.INSTANT and a.date_fact.confidence==Confidence.PROVEN and a.date_fact.value is not None:
                            if (a.date_fact.value.year,a.date_fact.value.month)!=(tzfact.year,tzfact.month):
                                a.date_fact=DateFact(DatePrecision.UNKNOWN,Confidence.CONFLICT,source='timezone-chronology-conflict',note='Proven GPS timezone and existing exact chronology disagree on month')
                                a.warnings.append('proven timezone chronology conflicts with existing exact chronology')
                        else:
                            a.date_fact=tzfact
                    elif suggestion is not None:
                        a.warnings.append(f'probable timezone suggestion retained without upgrading chronology: {suggestion.isoformat()}')
                except Exception as e:
                    a.warnings.append(f'timezone enrichment failed: {e}')
            if self.place_resolver and a.location_fact.exact:
                try:
                    if hasattr(self.place_resolver,'nearest'):
                        a.place_evidence=self.place_resolver.nearest(a.location_fact.lat,a.location_fact.lon)
                    else:
                        a.place_evidence=self.place_resolver(a.location_fact.lat,a.location_fact.lon)
                except Exception as e:
                    a.warnings.append(f'place enrichment failed: {e}')
            a.placement_fact=a.date_fact
            a.output_dir=placement_dir(a.placement_fact)
            original=PurePosixPath(a.occurrences[0].path).name
            a.output_name=portable_filename(original)
        assets=sorted(groups.values(),key=lambda a:a.asset_id)
        by_id={a.asset_id:a for a in assets}
        fam_evidence=[EmbeddedEvidence(a.asset_id,a.occurrences[0].path,a.embedded.content_identifier if a.embedded else None) for a in assets]
        for relation in match_live_photos(fam_evidence):
            apply_live_photo_placement(by_id,relation)
        for relation in variant_hints(fam_evidence):
            rel={'kind':relation.kind,'members':relation.member_ids,'confidence':relation.confidence.value,'key':relation.key,'warnings':relation.warnings}
            for aid in relation.member_ids:
                by_id[aid].family_relations.append(rel)
                by_id[aid].warnings.extend(relation.warnings)
        final_names=resolve_filename_collisions([(a.asset_id,a.output_dir or '',a.output_name or '_') for a in assets])
        for a in assets:a.output_name=final_names[a.asset_id]
        return assets,matched_sidecars,album_json

    def plan(self)->list[LogicalAsset]:
        members=self.lib.scan();media=[m for m in members if m.suffix in MEDIA_SUFFIXES]
        hashes=self.lib.batch_sha256(media);sidx=SidecarIndex(members,self.lib)
        return self._build(members,hashes,sidx)[0]

    def plan_report(self)->PlanningReport:
        start_stats={}
        for a in self.lib.archives:
            try:
                st=os.stat(a);start_stats[a]=(st.st_size,st.st_mtime_ns,sha256_file(Path(a)))
            except OSError:pass
        members,problems=self.lib.scan_tolerant();media=[m for m in members if m.suffix in MEDIA_SUFFIXES]
        hashes,hash_problems=self.lib.batch_sha256_tolerant(media)
        sidx=SidecarIndex(members,self.lib,tolerant=True)
        assets,matched_sidecars,album_json=self._build(members,hashes,sidx)
        problems=[*problems,*hash_problems,*sidx.problems]
        end_stats={}
        for a in self.lib.archives:
            try:
                st=os.stat(a);end_stats[a]=(st.st_size,st.st_mtime_ns,sha256_file(Path(a)))
            except OSError:continue
            if a in start_stats and start_stats[a]!=end_stats[a]:
                problems.append(SourceProblem(a,None,'archive_changed','archive changed during planning'))
        classified=set((m.archive,m.path) for m in media if (m.archive,m.path) in hashes)|matched_sidecars|album_json
        unclassified=[]
        for m in members:
            key=(m.archive,m.path)
            if key in classified:continue
            unclassified.append(m.ref)
        return PlanningReport(assets,problems,unclassified,end_stats)
