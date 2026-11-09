#!/usr/bin/env python3
"""Create isolated C5 source copy; never overwrite existing work."""
from pathlib import Path
import hashlib,json,shutil,subprocess,tempfile
root=Path(__file__).resolve().parent
base=root.parent/'reference/linux-6.12'
target=root/'linux-6.12'
if target.exists(): raise SystemExit('Working source already exists; preserved. Use build.sh.')
hashes=json.loads((root/'source-hashes.json').read_text())
for name,values in hashes.items():
    p=base/name
    actual=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    if actual!=values['base']: raise SystemExit('Pristine-source mismatch: '+name)
with tempfile.TemporaryDirectory(prefix='.prepare-',dir=root) as directory:
    staged=Path(directory)/'linux-6.12'
    shutil.copytree(base,staged)
    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(root.parent/'linux.patch')],cwd=staged,check=True)
    for name,values in hashes.items():
        if hashlib.sha256((staged/name).read_bytes()).hexdigest()!=values['modified']:
            raise SystemExit('Patched-source mismatch: '+name)
    if target.exists():raise SystemExit('Another preparation created the target; preserved.')
    staged.rename(target)
print('C5 source prepared; pristine reference unchanged. No board accessed.')
