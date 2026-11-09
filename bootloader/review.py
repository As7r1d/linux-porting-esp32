#!/usr/bin/env python3
"""Offline review of candidate erase sectors against a full owner backup."""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def review(backup):
    from runpy import run_path
    module=run_path(str(ROOT/'build.py'))
    module['verify'](ROOT,ROOT/'build/SHA256SUMS')
    data=backup.read_bytes()
    if len(data)!=0x800000:raise ValueError('Requires exact 8 MiB backup')
    plan=json.loads((ROOT/'build/plan.json').read_text())
    report=dict(backup=str(backup.resolve()),backup_sha256=hashlib.sha256(data).hexdigest(),
        backup_bytes=len(data),board_freshness_verified=False,flash_written=False,hardware_tested=False,regions=[])
    for region in plan['regions']:
        a,b=region['erase_start'],region['erase_end']
        old=data[a:b]
        row=dict(region,old_sector_sha256=hashlib.sha256(old).hexdigest(),old_non_ff_bytes=sum(x!=255 for x in old))
        report['regions'].append(row)
        print(f"{region['file']}: sectors {a:#x}..{b:#x}; existing non-FF bytes={row['old_non_ff_bytes']}")
    print('Backup SHA256:',report['backup_sha256'])
    print('OFFLINE ONLY: security state and backup freshness must be checked on board; no Flash write performed.')
    return report
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('backup',type=Path);p.add_argument('--report',type=Path)
    args=p.parse_args();result=review(args.backup)
    if args.report:
        with args.report.open('x') as out:out.write(json.dumps(result,indent=2)+'\n')
