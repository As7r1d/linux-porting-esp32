#!/usr/bin/env python3
"""Reviewed C5 Flash writer. Default is offline plan only; --write overwrites listed sectors."""
import argparse,hashlib,json,re,subprocess,sys,tempfile
from pathlib import Path
from runpy import run_path
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent

def security_identity(text):
    for token in ['Secure Boot: Disabled','Flash Encryption: Disabled','Chip ID: 23']:
        if token not in text:raise ValueError('Unsupported/missing security state: '+token)
    if not re.search(r'Flags:\s*0x0+\s',text):raise ValueError('Security flags must be zero')
    match=re.search(r'BASE MAC:\s*([0-9a-fA-F:]{17})',text)
    if not match:raise ValueError('Missing board BASE MAC')
    return match[1].lower()

def validate(preflight, previous_write=None, restoring=False):
    run_path(str(ROOT/'build.py'))['verify'](ROOT,ROOT/'build/SHA256SUMS')
    backup=(preflight/'flash-8MiB.bin').read_bytes()
    report=json.loads((preflight/'review.json').read_text())
    if len(backup)!=0x800000 or hashlib.sha256(backup).hexdigest()!=report['backup_sha256']:
        raise ValueError('Full backup size/hash mismatch')
    identity=security_identity((preflight/'security.txt').read_text())
    plan=json.loads((ROOT/'build/plan.json').read_text())
    if len(plan['regions'])!=5 or [(r['offset'],r['file']) for r in plan['regions']]!=[
        (0x2000,'bootloader.bin'),(0x10000,'metadata.bin'),(0x20000,'kernel.bin'),
        (0x220000,'dtb.bin'),(0x230000,'rootfs.cramfs')]:
        raise ValueError('Unexpected Flash layout')
    if restoring:
        # Recovery is tied to the owner backup, not the current candidate layout.
        for old in report['regions']:
            a,b=old['erase_start'],old['erase_end']
            if not 0<=a<b<=len(backup) or hashlib.sha256(backup[a:b]).hexdigest()!=old['old_sector_sha256']:
                raise ValueError('Recovery backup sector review mismatch')
        return backup,identity,plan,backup
    for region,old in zip(plan['regions'],report['regions'],strict=True):
        keys=['offset','file','erase_start','erase_end'] if previous_write else ['offset','file','bytes','erase_start','erase_end','sha256']
        for key in ([] if restoring else keys):
            if region[key]!=old[key]:raise ValueError('Plan changed since backup review: '+key)
        data=backup[old['erase_start']:old['erase_end']]
        if hashlib.sha256(data).hexdigest()!=old['old_sector_sha256']:raise ValueError('Old-sector hash mismatch')
    baseline=backup
    if previous_write:
        baseline=installed_baseline(backup,previous_write,report['regions'])
    return backup,identity,plan,baseline

def installed_baseline(original,previous_write,regions):
    """Reconstruct only known installed sectors; live reads still must match.

    Original recovery backup is never changed. All erase bounds must be identical
    to its reviewed ranges; a different layout needs a new full preflight.
    """
    result=json.loads((previous_write/'result.json').read_text())
    if result.get('action')!='write' or result.get('readback_verified') is not True:
        raise ValueError('Previous write lacks successful readback result')
    old=json.loads((previous_write/'plan.json').read_text())['regions']
    if len(old)!=len(regions):raise ValueError('Previous region count mismatch')
    for a,b in zip(old,regions,strict=True):
        if any(a[k]!=b[k] for k in ['offset','file','erase_start','erase_end']):
            raise ValueError('Changed layout needs full preflight')
    baseline=bytearray(original)
    for n,r in enumerate(old[1:]+old[:1]):
        data=(previous_write/f'after-{n}.bin').read_bytes()
        if data!=(previous_write/r['file']).read_bytes() or len(data)!=r['bytes'] or hashlib.sha256(data).hexdigest()!=r['sha256']:
            raise ValueError('Previous payload/readback mismatch')
        a,b=r['erase_start'],r['erase_end']
        if not 0<=a<=r['offset']<r['offset']+len(data)<=b<=len(original):
            raise ValueError('Previous erase bounds invalid')
        baseline[a:b]=bytes([255])*(b-a)
        baseline[r['offset']:r['offset']+len(data)]=data
    return bytes(baseline)

def program(backup,identity,plan,work,run,restore=False):
    # Freeze payload bytes before opening the board; concurrent builds cannot
    # substitute a different image after the offline review.
    if not restore:
        for r in plan['regions']:
            data=(ROOT/'build'/r['file']).read_bytes()
            if len(data)!=r['bytes'] or hashlib.sha256(data).hexdigest()!=r['sha256']:
                raise ValueError('Payload changed before write: '+r['file'])
            (work/r['file']).write_bytes(data)
    live=run(['get-security-info'],rom=True)
    if security_identity(live)!=identity:raise ValueError('Board identity differs from backup')
    if restore:
        source=work/'restore-8MiB.bin';source.write_bytes(backup)
        run(['write-flash','--flash-mode','keep','--flash-freq','keep','--flash-size','keep','0',str(source)])
        dest=work/'restore-readback.bin'
        run(['read-flash','--flash-size','8MB','0','0x800000',str(dest)])
        if dest.read_bytes()!=backup:raise ValueError('Restore readback differs; no success claim')
        return
    # Check ALL intended erase sectors before the first write.
    for n,r in enumerate(plan['regions']):
        dest=work/f'before-{n}.bin'
        run(['read-flash','--flash-size','8MB',hex(r['erase_start']),hex(r['erase_end']-r['erase_start']),str(dest)])
        if dest.read_bytes()!=backup[r['erase_start']:r['erase_end']]:
            raise ValueError('Board contents changed since backup; no writes performed')
    # Boot image last. This reduces risk; it is not transactional rollback.
    ordered=plan['regions'][1:]+plan['regions'][:1]
    for n,r in enumerate(ordered):
        source=work/r['file']
        data=source.read_bytes()
        padded=data+bytes([255])*(r['erase_end']-r['offset']-len(data))
        if backup[r['erase_start']:r['erase_end']]==padded:
            observed=(work/f"before-{plan['regions'].index(r)}.bin").read_bytes()
            (work/f'after-{n}.bin').write_bytes(observed[:len(data)])
            print('UNCHANGED / READ VERIFIED:',r['file'],flush=True)
            continue
        run(['write-flash','--flash-mode','keep','--flash-freq','keep','--flash-size','keep',hex(r['offset']),str(source)])
        dest=work/f'after-{n}.bin'
        run(['read-flash','--flash-size','8MB',hex(r['offset']),hex(r['bytes']),str(dest)])
        if dest.read_bytes()!=source.read_bytes():raise ValueError('Readback mismatch: '+r['file'])
        print('VERIFIED:',r['file'],flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--preflight',required=True,type=Path);p.add_argument('--port',default='/dev/ttyACM0')
    mode=p.add_mutually_exclusive_group();mode.add_argument('--write',action='store_true');mode.add_argument('--restore',action='store_true')
    p.add_argument('--previous-write',type=Path,help='Use verified installed-sector baseline from this write log; keep original recovery backup')
    args=p.parse_args()
    if args.restore and args.previous_write:p.error('--restore uses original backup; omit --previous-write')
    backup,identity,plan,baseline=validate(args.preflight,args.previous_write,restoring=args.restore)
    if args.previous_write: print('Installed baseline from verified write:',args.previous_write)
    print('Backup:',args.preflight/'flash-8MiB.bin');print('Board BASE MAC:',identity)
    for r in plan['regions']:print(f"{r['file']}: erase {r['erase_start']:#x}..{r['erase_end']:#x} (end exclusive)")
    print('This overwrites existing bootloader/firmware; no eFuse writes. Cold boot remains untested.')
    if not args.write and not args.restore:
        print('OFFLINE PLAN ONLY: no serial port opened. --write programs candidate; --restore restores entire8MiB backup.')
        return 0
    import esptool
    if esptool.__version__!='5.6.0':raise ValueError('Requires reviewed esptool5.6.0')
    work=Path(tempfile.mkdtemp(prefix='flash-boot-write-',dir=PROJECT/'logs'))
    (work/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    print('Action log:',work,flush=True)
    seq=0
    def run(command,rom=False):
        nonlocal seq
        argv=[sys.executable,'-B','-m','esptool','--chip','esp32c5','--port',args.port,'--after','no-reset']
        if rom:argv+=['--no-stub']
        argv+=command
        seq+=1
        with (work/f'{seq:02d}.txt').open('x') as log:
            process=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            lines=[]
            for line in process.stdout:
                print(line,end='',flush=True);log.write(line);log.flush();lines.append(line)
            if process.wait()!=0:raise RuntimeError('esptool failed; stop, preserve logs; no automatic retry/reset/restore')
        return ''.join(lines)
    program(backup if args.restore else baseline,identity,plan,work,run,restore=args.restore)
    (work/'result.json').write_text(json.dumps(dict(action='restore' if args.restore else 'write',readback_verified=True,cold_boot_verified=False,board_left_in_download_mode=True),indent=2)+'\n')
    print('READBACK VERIFIED; board remains in download mode. Cold boot not yet tested. No automatic reset.')
    return 0
if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as error:
        print(f'FAILED: {type(error).__name__}: {error}',file=sys.stderr)
        print('No automatic retry/reset/rollback. A failed write can leave a partial image.',file=sys.stderr)
        sys.exit(1)
