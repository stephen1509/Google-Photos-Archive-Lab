from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .exiftool import ExifToolAdapter, ExifToolError
from .exiftool_distribution import distribution_tree_sha256
from .fingerprint import jpeg_payload_fingerprint
from .transactions import sha256_file, write_json_new
from .writepolicy import MetadataPlan

QUALIFICATION_SCHEMA='gpa.exiftool-qualification.v1'
HARNESS_ID='gpa.exiftool-jpeg-roundtrip.v1'

class ExifToolQualificationError(RuntimeError):pass

@dataclass
class ExifToolQualificationReport:
    schema:str=QUALIFICATION_SCHEMA
    harness:str=HARNESS_ID
    created_utc:str=''
    qualification_id:str=''
    passed:bool=False
    executable:str=''
    version:str|None=None
    executable_sha256:str|None=None
    distribution_root:str|None=None
    distribution_sha256:str|None=None
    checks:dict[str,bool]=field(default_factory=dict)
    observations:dict=field(default_factory=dict)
    errors:list[str]=field(default_factory=list)

    def to_dict(self)->dict:return asdict(self)


def _sha_bytes(data:bytes)->str:return hashlib.sha256(data).hexdigest()


def fingerprint_exiftool_candidate(executable:Path,distribution_root:Path|None=None)->dict:
    exe=Path(executable)
    if exe.is_symlink():raise ExifToolQualificationError('ExifTool candidate executable must not be a symlink')
    if not exe.is_file():raise ExifToolQualificationError(f'ExifTool candidate executable not found: {exe}')
    resolved=exe.resolve();out={'executable':str(resolved),'executable_sha256':sha256_file(resolved)}
    if distribution_root is not None:
        dr=Path(distribution_root).resolve()
        try:resolved.relative_to(dr)
        except ValueError as e:raise ExifToolQualificationError('ExifTool executable is not inside the declared distribution root') from e
        out['distribution_root']=str(dr);out['distribution_sha256']=distribution_tree_sha256(dr)
    else:
        out['distribution_root']=None;out['distribution_sha256']=None
    return out


def _candidate_version(executable:Path,timeout_seconds:float)->str:
    command=[str(executable),'-ver']
    # Windows cannot directly execute a shebang Python qualification fixture.
    # Real ExifTool candidates are .exe files and keep their native launch path.
    if executable.suffix.casefold()=='.py':command=[sys.executable,*command]
    try:cp=subprocess.run(command,capture_output=True,text=True,timeout=timeout_seconds)
    except subprocess.TimeoutExpired as e:raise ExifToolQualificationError(f'ExifTool version check timed out after {timeout_seconds:g}s') from e
    if cp.returncode:raise ExifToolQualificationError(cp.stderr.strip() or 'ExifTool version check failed')
    v=cp.stdout.strip()
    if not v:raise ExifToolQualificationError('ExifTool returned an empty version')
    return v


def _fixture(path:Path)->None:
    from PIL import Image
    im=Image.new('RGB',(17,13))
    # Stable non-flat pixels so accidental recompression/content changes are obvious.
    pix=im.load()
    for y in range(im.height):
        for x in range(im.width):pix[x,y]=((x*17+y*3)%256,(x*5+y*19)%256,(x*11+y*7)%256)
    im.save(path,'JPEG',quality=91,subsampling=0)


def _find_suffix(tags:dict,suffix:str):
    if suffix in tags:return tags[suffix]
    for k,v in tags.items():
        if str(k).split(':')[-1]==suffix:return v
    return None


def _as_float(v):
    try:return float(v)
    except (TypeError,ValueError):return None


def _qualification_id(version:str,exe_sha:str,dist_sha:str|None)->str:
    payload={'harness':HARNESS_ID,'version':version,'executable_sha256':exe_sha,'distribution_sha256':dist_sha}
    return _sha_bytes(json.dumps(payload,sort_keys=True,separators=(',',':')).encode())[:24]


def qualify_exiftool_candidate(executable:Path,workdir:Path,*,distribution_root:Path|None=None,subprocess_timeout_seconds:float=30.0)->ExifToolQualificationReport:
    if subprocess_timeout_seconds<=0:raise ValueError('subprocess_timeout_seconds must be > 0')
    workdir=Path(workdir);workdir.mkdir(parents=True,exist_ok=True)
    report=ExifToolQualificationReport(created_utc=datetime.now(timezone.utc).isoformat())
    try:
        fp=fingerprint_exiftool_candidate(Path(executable),distribution_root)
        report.executable=fp['executable'];report.executable_sha256=fp['executable_sha256'];report.distribution_root=fp['distribution_root'];report.distribution_sha256=fp['distribution_sha256']
        version=_candidate_version(Path(report.executable),subprocess_timeout_seconds);report.version=version
        report.qualification_id=_qualification_id(version,report.executable_sha256,report.distribution_sha256)
        report.checks['version_nonempty']=bool(version)
        report.checks['executable_fingerprinted']=len(report.executable_sha256 or '')==64
        if distribution_root is not None:report.checks['distribution_fingerprinted']=len(report.distribution_sha256 or '')==64

        fixture=workdir/'qualification.jpg';_fixture(fixture)
        before=fixture.read_bytes();before_sha=_sha_bytes(before);before_payload=jpeg_payload_fingerprint(before)
        plan=MetadataPlan(
            exif={
                'DateTimeOriginal':'2017:05:01 21:30:00','OffsetTimeOriginal':'+09:00',
                'GPSLatitude':'34.6851','GPSLatitudeRef':'N','GPSLongitude':'135.8048','GPSLongitudeRef':'E',
            },
            xmp={'XMP-photoshop:DateCreated':'2017-05-01T21:30:00+09:00','dc:description':'GPA qualification fixture'},
        )
        adapter=ExifToolAdapter(report.executable,approved_versions=(version,),timeout_seconds=subprocess_timeout_seconds)
        try:
            report.observations['write_output']=adapter.write(fixture,plan)
            report.checks['write_completed']=True
        except Exception as e:
            report.checks['write_completed']=False;report.errors.append(f'write failed: {e}')

        if fixture.exists():
            after=fixture.read_bytes();after_sha=_sha_bytes(after)
            report.observations.update({'before_sha256':before_sha,'after_sha256':after_sha})
            report.checks['whole_file_changed']=after_sha!=before_sha
            try:report.checks['jpeg_payload_unchanged']=jpeg_payload_fingerprint(after)==before_payload
            except Exception as e:report.checks['jpeg_payload_unchanged']=False;report.errors.append(f'payload fingerprint failed: {e}')
            try:
                from PIL import Image
                with Image.open(fixture) as im:im.verify()
                report.checks['output_decodable']=True
            except Exception as e:report.checks['output_decodable']=False;report.errors.append(f'output decode failed: {e}')
        else:
            report.checks['whole_file_changed']=False;report.checks['jpeg_payload_unchanged']=False;report.checks['output_decodable']=False;report.errors.append('candidate removed qualification fixture')

        try:
            tags=adapter.read(fixture);report.checks['readback_completed']=True
            report.observations['readback_keys']=sorted(str(k) for k in tags.keys())
            report.checks['readback_date']=str(_find_suffix(tags,'DateTimeOriginal') or '').startswith('2017:05:01 21:30:00')
            report.checks['readback_offset']=str(_find_suffix(tags,'OffsetTimeOriginal') or '')=='+09:00'
            lat=_as_float(_find_suffix(tags,'GPSLatitude'));lon=_as_float(_find_suffix(tags,'GPSLongitude'))
            latref=str(_find_suffix(tags,'GPSLatitudeRef') or 'N').upper();lonref=str(_find_suffix(tags,'GPSLongitudeRef') or 'E').upper()
            if lat is not None and latref=='S':lat=-abs(lat)
            if lon is not None and lonref=='W':lon=-abs(lon)
            report.checks['readback_gps']=lat is not None and lon is not None and abs(lat-34.6851)<1e-5 and abs(lon-135.8048)<1e-5
            desc=_find_suffix(tags,'Description');report.checks['readback_description']=desc=='GPA qualification fixture' or (isinstance(desc,list) and 'GPA qualification fixture' in desc)
            dc=_find_suffix(tags,'DateCreated');report.checks['readback_xmp_date']=str(dc or '').replace(':','-',2).replace(' ','T',1).startswith('2017-05-01T21:30:00')
        except Exception as e:
            report.checks['readback_completed']=False;report.errors.append(f'readback failed: {e}')
            for k in ('readback_date','readback_offset','readback_gps','readback_description','readback_xmp_date'):report.checks.setdefault(k,False)
    except Exception as e:
        report.errors.append(str(e))
    required={
        'version_nonempty','executable_fingerprinted','write_completed','whole_file_changed','jpeg_payload_unchanged','output_decodable',
        'readback_completed','readback_date','readback_offset','readback_gps','readback_description','readback_xmp_date',
    }
    if distribution_root is not None:required.add('distribution_fingerprinted')
    report.passed=all(report.checks.get(k) is True for k in required)
    return report


def write_qualification_report(path:Path,report:ExifToolQualificationReport)->Path:
    path=Path(path);write_json_new(path,report.to_dict());return path
