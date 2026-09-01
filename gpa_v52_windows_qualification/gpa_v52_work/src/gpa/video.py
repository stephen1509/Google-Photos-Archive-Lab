from __future__ import annotations
import json, subprocess
from pathlib import Path

class ProbeError(Exception):pass
ALLOWED={'h264','hevc','av1'}

def probe_video(path:Path,ffprobe='ffprobe')->dict:
    cp=subprocess.run([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True)
    if cp.returncode:raise ProbeError(cp.stderr.strip() or 'ffprobe failed')
    try:return json.loads(cp.stdout)
    except json.JSONDecodeError as e:raise ProbeError('invalid ffprobe json') from e

def has_supported_video_track(probe:dict)->bool:
    for s in probe.get('streams',[]):
        if s.get('codec_type')=='video' and s.get('codec_name') in ALLOWED:return True
    return False
