#!/usr/bin/env python3
"""Offline regression for target-root symlinks and cached extraction retries."""
from pathlib import Path
import io, os, runpy, subprocess, tarfile, tempfile, unittest, shutil
SCRIPTS=Path(__file__).resolve().parent
unpack=runpy.run_path(str(SCRIPTS/'buildroot-archive.py'))['unpack']
ROOT='buildroot-2025.02.19'
def archive(path, entries, mode="w:xz"):
    with tarfile.open(path,mode) as tar:
        for name,kind,data in entries:
            member=tarfile.TarInfo(name)
            if kind=='file':
                member.size=len(data);member.mode=0o755;tar.addfile(member,io.BytesIO(data))
            else:
                member.type=tarfile.SYMTYPE if kind=='sym' else tarfile.LNKTYPE
                member.linkname=data;tar.addfile(member)
class Checks(unittest.TestCase):
    def test_absolute_and_relative_target_links_created_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);a=p/'source.tar.xz'
            archive(a,[(ROOT+'/etc/sysv-rcs','sym','/etc/init.d/rcS'),(ROOT+'/relative','sym','../target'),(ROOT+'/file','file',b'contents'),(ROOT+'/hard','hard',ROOT+'/file')])
            links=unpack(a,p/'out',ROOT)
            self.assertEqual(os.readlink(p/'out'/ROOT/'etc/sysv-rcs'),'/etc/init.d/rcS')
            self.assertEqual((p/'out'/ROOT/'hard').read_bytes(),b'contents')
            self.assertEqual(len(links),2)
    def test_bzip2_source_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);a=p/'source.tar.bz2'
            archive(a,[(ROOT+'/file','file',b'bzip2 source')],mode='w:bz2')
            unpack(a,p/'out',ROOT)
            self.assertEqual((p/'out'/ROOT/'file').read_bytes(),b'bzip2 source')
    def test_reject_link_descendants_traversal_duplicates_and_external_hardlinks(self):
        cases=[[(ROOT+'/escape','sym','/tmp'),(ROOT+'/escape/file','file',b'bad')],
               [(ROOT+'/../outside','file',b'bad')],
               [(ROOT+'/same','file',b'one'),(ROOT+'/same','sym','/tmp')],
               [(ROOT+'/hard','hard','/etc/passwd')]]
        for entries in cases:
            with self.subTest(entries=entries),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);archive(p/'a.tar.xz',entries)
                with self.assertRaises(ValueError):unpack(p/'a.tar.xz',p/'out',ROOT)
                self.assertFalse((p/'out').exists())
    def test_fetch_preserves_cache_after_failure_and_reuses_without_curl(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'scripts').mkdir();(p/'reference').mkdir()
            for n in ['fetch-buildroot-reference.sh','buildroot-archive.py']:shutil.copy2(SCRIPTS/n,p/'scripts'/n)
            a=p/'reference'/(ROOT+'.tar.xz');sig=Path(str(a)+'.sign');sig.write_bytes(b'fixture signature, not verified')
            archive(a,[(ROOT+'/../outside','file',b'bad')])
            (p/'bin').mkdir();curl=p/'bin/curl';curl.write_text('#!/bin/sh\nexit 99\n');curl.chmod(0o755)
            env=dict(os.environ,PATH=str(p/'bin')+':'+os.environ['PATH'])
            command=['bash',str(p/'scripts/fetch-buildroot-reference.sh')]
            fail=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertNotEqual(fail.returncode,0);self.assertTrue(a.exists());self.assertTrue(sig.exists())
            archive(a,[(ROOT+'/Makefile','file',b'export BR2_VERSION := 2025.02.19\n'),
                       (ROOT+'/configs/qemu_riscv32_nommu_virt_defconfig','file',b'BR2_riscv=y\n'),
                       (ROOT+'/package/skeleton/etc/link','sym','/etc/init.d/rcS')])
            success=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(success.returncode,0,success.stderr)
            self.assertIn('READY:',success.stdout)
            self.assertTrue((p/'reference'/ROOT/'Makefile').exists())
            self.assertTrue((p/'reference'/(ROOT+'-source-links.json')).exists())
if __name__=='__main__':unittest.main()
