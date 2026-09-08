#!/usr/bin/env python
from pathlib import Path
import argparse,json
p=argparse.ArgumentParser();p.add_argument('file',type=Path);a=p.parse_args();d=json.loads(a.file.read_text());print(json.dumps({'regionId':d.get('regionId'),'regionName':d.get('regionName'),'kind':d.get('kind'),'sections':list((d.get('sections') or {}).keys()),'providers':d.get('providers')},indent=2))
