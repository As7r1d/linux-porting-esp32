#!/usr/bin/env python3
"""Publication regression tests: reject leaks, check source boundaries, strip metadata."""
from pathlib import Path
import contextlib,importlib.util,io,tarfile,tempfile,unittest
HERE=Path(__file__).resolve().parent

class Checks(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('public_source',HERE/'public-source.py')
        self.tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.tool)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.tool.ROOT=self.root
        for name in self.tool.ROOT_FILES-{'LICENSE'}:(self.root/name).write_text('source\n')
        (self.root/'scripts').mkdir()

    def test_archive_contains_only_reviewed_source_and_anonymous_metadata(self):
        (self.root/'.local').mkdir();(self.root/'.local/flash.bin').write_bytes(b'private')
        (self.root/'logs').mkdir();(self.root/'logs/identity.json').write_text('private')
        with contextlib.redirect_stdout(io.StringIO()):
            self.tool.export()
        archive=next((self.root/'public-output').glob('*.tar.gz'))
        with tarfile.open(archive) as t:
            members=t.getmembers()
            self.assertTrue(members)
            for m in members:
                self.assertNotIn('/logs/',m.name);self.assertNotIn('/.local/',m.name)
                self.assertEqual((m.uid,m.gid,m.uname,m.gname,m.mtime),(0,0,'','',0))
                self.assertTrue(m.isfile())

    def test_private_patterns_and_symlinks_are_rejected(self):
        p=self.root/'scripts/test.py'
        samples=['/'+'home/'+'private-user/file', 'BEGIN '+'OPENSSH PRIVATE KEY',
                 'gh'+'p_'+'A'*30, ':'.join(['aa','bb','cc','dd','ee','ff']),
                 'C5PERSIST:'+'a'*32]
        for sample in samples:
            with self.subTest(sample=sample):
                p.write_text(sample)
                with self.assertRaises(ValueError):self.tool.checked_files()
        p.unlink();p.symlink_to(self.root/'README.md')
        with self.assertRaises(ValueError):self.tool.checked_files()

    def test_unreviewed_or_missing_sources_fail_closed(self):
        with contextlib.redirect_stdout(io.StringIO()):self.tool.checked_files()
        extra=self.root/'unreviewed.txt';extra.write_text('private')
        with self.assertRaises(ValueError):self.tool.checked_files()
        extra.unlink()
        (self.root/'linux.config').unlink()
        with self.assertRaises(ValueError):self.tool.checked_files()

if __name__=='__main__':unittest.main()
