#!/usr/bin/env python3
"""Explicit source-only publication; never include private evidence or dependencies."""
from pathlib import Path
import datetime,gzip,io,os,re,tarfile,sys

ROOT=Path(__file__).resolve().parent.parent
ROOT_FILES={'.gitignore','README.md','BUILDING.md','NOTICE.md','LICENSE',
            'Makefile','build.sh','linux.config','linux.patch','esp32c5.dts'}
SOURCE_DIRS={'bootloader','kernel','user','scripts','docs','LICENSES'}
SKIP_DIRS={'build','output','smoke','logs','checkpoints','linux-6.12','__pycache__'}
SKIP_FILES={'built.json','configured.json','configured-SHA256SUMS'}
SUFFIXES={'.c','.h','.S','.ld','.py','.sh','.md','.json','.config','.fragment','.txt','.hash'}

def candidates():
    missing=[name for name in ROOT_FILES-{'LICENSE'} if not (ROOT/name).is_file()]
    if missing:raise ValueError('Missing required sources: '+', '.join(sorted(missing)))
    local_dirs={'.local','logs','reference','public-output','.venv','.git','__pycache__','.agents','.codex','.aws'}
    for path in ROOT.iterdir():
        if path.name in local_dirs:continue
        if path.is_dir() and path.name in SOURCE_DIRS:continue
        if path.name in ROOT_FILES:continue
        raise ValueError('Unreviewed repository-root entry: '+path.name)
    paths=[ROOT/name for name in sorted(ROOT_FILES) if (ROOT/name).exists()]
    for folder in sorted(SOURCE_DIRS):
        for base,dirs,files in os.walk(ROOT/folder,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in SKIP_DIRS)
            for d in dirs:
                if (Path(base)/d).is_symlink():raise ValueError('Source directory symlink: '+str(Path(base)/d))
            for name in sorted(files):
                if name in SKIP_FILES:continue
                p=Path(base)/name
                if p.suffix not in SUFFIXES and name not in {'Makefile','c5_defconfig','init','c5-proof','storage-status'}:
                    raise ValueError('Unexpected file in source tree: '+str(p.relative_to(ROOT)))
                paths.append(p)
    return sorted(paths)

def read_source(path):
    if path.is_symlink():raise ValueError('Source file symlink: '+str(path))
    return path.read_bytes()

def inspect(path,data):
    if path.is_symlink():raise ValueError('Source file symlink: '+str(path))
    text=data.decode('utf-8')
    patterns=[r'/' + r'home/[^/\s\"\x27]+/',r'BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY',
              r'AKIA[0-9A-Z]{16}',r'gh[pousr]_[A-Za-z0-9]{25,}',
              r'C5PERSIST:[0-9a-f]{32}']
    for pattern in patterns:
        if re.search(pattern,text):raise ValueError('Private-data pattern in '+str(path.relative_to(ROOT)))
    for address in re.findall(r'(?<![\w:])(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}(?![\w:])',text):
        if address.lower() not in {'02:00:00:00:00:01','00:00:00:00:00:00'}:
            raise ValueError('Non-placeholder device MAC in '+str(path.relative_to(ROOT)))

def checked_files():
    frozen={}
    for p in candidates():
        data=read_source(p);inspect(p,data)
        frozen[p.relative_to(ROOT).as_posix()]=(data,0o755 if p.stat().st_mode&0o111 else 0o644)
    return frozen

def export():
    files=checked_files()
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    base=ROOT/'public-output';base.mkdir(exist_ok=True)
    dest=base/('esp32c5-linux-'+stamp);dest.mkdir()
    archive=dest.with_suffix('.tar.gz')
    with archive.open('xb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as compressed,tarfile.open(fileobj=compressed,mode='w') as tar:
        for name,(data,mode) in sorted(files.items()):
            p=dest/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(mode)
            info=tarfile.TarInfo('esp32c5-linux/'+name);info.size=len(data);info.mode=mode
            info.uid=info.gid=info.mtime=0;info.uname=info.gname=''
            tar.addfile(info,io.BytesIO(data))
    print('Public source directory:',dest)
    print('Public archive:',archive)
    print('No Git initialization, remote, upload or push performed.')

def main():
    mode=sys.argv[1] if len(sys.argv)>1 else 'check'
    if mode=='check':print('PASS:',len(checked_files()),'public files; source allowlist, symlinks and private-data patterns checked')
    elif mode=='export':export()
    else:raise ValueError('Use check or export')

if __name__=='__main__':
    try:main()
    except (ValueError,UnicodeError) as error:raise SystemExit(str(error))
