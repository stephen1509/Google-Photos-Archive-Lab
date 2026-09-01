from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
import json, re
from .transactions import sha256_file
from .takeout_parts import analyze_takeout_parts

_SCHEMA='gpa.takeout-receipt.v1'
_PART_RE=re.compile(r'^(?P<base>.+-)(?P<num>\d{3})(?P<ext>\.zip)$',re.I)

@dataclass
class ReceiptPart:
    name:str
    sha256:str
    size:int
    part_number:int|None=None

@dataclass
class TakeoutReceipt:
    schema:str=_SCHEMA
    export_id:str=''
    captured_at:str=''
    confirmation_source:str='user_confirmed_from_takeout_ui_or_email'
    expected_part_count:int=0
    parts:list[ReceiptPart]=field(default_factory=list)
    notes:list[str]=field(default_factory=list)

    def to_dict(self)->dict:
        d=asdict(self);d['parts']=[asdict(x) for x in self.parts];return d

@dataclass
class ReceiptVerification:
    ok:bool
    receipts_checked:int=0
    expected_parts:int=0
    matched_parts:int=0
    covered_run_hashes:set[str]=field(default_factory=set)
    problems:list[str]=field(default_factory=list)


def create_takeout_receipt(paths,*,expected_part_count:int,export_id:str|None=None,confirmation_source:str='user_confirmed_from_takeout_ui_or_email',notes:list[str]|None=None)->TakeoutReceipt:
    paths=[Path(x) for x in paths]
    if expected_part_count<1:raise ValueError('expected_part_count must be at least 1')
    if len(paths)>expected_part_count:raise ValueError('more downloaded files were supplied than the confirmed expected part count')
    rows=[]
    for p in paths:
        st=p.stat();m=_PART_RE.match(p.name)
        rows.append(ReceiptPart(p.name,sha256_file(p),st.st_size,int(m.group('num')) if m else None))
    if export_id is None:
        # If standard Takeout names are present, derive only a display/grouping label;
        # this is not treated as a Google-issued stable export identifier.
        std=[_PART_RE.match(p.name) for p in paths]
        bases={m.group('base') for m in std if m}
        export_id=(next(iter(bases)).rstrip('-') if len(bases)==1 else 'takeout-export')
    receipt=TakeoutReceipt(export_id=export_id,captured_at=datetime.now(timezone.utc).isoformat(),confirmation_source=confirmation_source,expected_part_count=expected_part_count,parts=rows,notes=list(notes or []))
    pa=analyze_takeout_parts([p.name for p in paths])
    if pa.known_gaps:receipt.notes.append(f'Observed filenames contain {pa.known_gaps} known missing internal part(s) at receipt creation time.')
    if len(paths)!=expected_part_count:receipt.notes.append(f'Only {len(paths)} of {expected_part_count} confirmed expected part(s) were present when this receipt was created.')
    return receipt


def write_takeout_receipt(path:Path,receipt:TakeoutReceipt)->Path:
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(receipt.to_dict(),ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(path);return path


def load_takeout_receipt(path:Path)->TakeoutReceipt:
    d=json.loads(Path(path).read_text(encoding='utf-8'))
    if d.get('schema')!=_SCHEMA:raise ValueError(f'unsupported Takeout receipt schema: {d.get("schema")}')
    return TakeoutReceipt(schema=d['schema'],export_id=d.get('export_id',''),captured_at=d.get('captured_at',''),confirmation_source=d.get('confirmation_source',''),expected_part_count=int(d.get('expected_part_count') or 0),parts=[ReceiptPart(**x) for x in d.get('parts',[])],notes=list(d.get('notes') or []))


def verify_takeout_receipts(receipt_paths,run_sources:list[dict])->ReceiptVerification:
    paths=[Path(x) for x in receipt_paths or []]
    if not paths:return ReceiptVerification(False,problems=['No durable Takeout receipt was supplied.'])
    by_hash={str(x.get('sha256')):x for x in run_sources if x.get('sha256')}
    out=ReceiptVerification(True,receipts_checked=len(paths))
    seen_receipt_hashes=set()
    for rp in paths:
        try:r=load_takeout_receipt(rp)
        except Exception as e:
            out.ok=False;out.problems.append(f'{rp}: unreadable receipt: {e}');continue
        out.expected_parts+=r.expected_part_count
        if r.expected_part_count<1:
            out.ok=False;out.problems.append(f'{rp}: expected part count is not recorded');continue
        if len(r.parts)!=r.expected_part_count:
            out.ok=False;out.problems.append(f'{rp}: receipt contains {len(r.parts)} downloaded part record(s), expected {r.expected_part_count}')
        pa=analyze_takeout_parts([x.name for x in r.parts],expected_counts={})
        if pa.known_gaps:
            out.ok=False;out.problems.append(f'{rp}: receipt filenames contain {pa.known_gaps} known internal part gap(s)')
        nums=[x.part_number for x in r.parts if x.part_number is not None]
        if nums and len(nums)==len(r.parts):
            expected=set(range(1,r.expected_part_count+1));missing=sorted(expected-set(nums))
            if missing:
                out.ok=False;out.problems.append(f'{rp}: confirmed expected part number(s) missing from receipt: {missing}')
        for part in r.parts:
            seen_receipt_hashes.add(part.sha256)
            src=by_hash.get(part.sha256)
            if not src:
                out.ok=False;out.problems.append(f'{rp}: receipt part {part.name} ({part.sha256[:12]}…) is not referenced by any migration run')
                continue
            out.matched_parts+=1;out.covered_run_hashes.add(part.sha256)
    # Every source archive used by the migration should be covered by a receipt;
    # otherwise an incremental/second export could bypass the completeness evidence.
    uncovered=set(by_hash)-seen_receipt_hashes
    if uncovered:
        out.ok=False;out.problems.append(f'{len(uncovered)} migration source archive fingerprint(s) are not covered by any Takeout receipt')
    return out
