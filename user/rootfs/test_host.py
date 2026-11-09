#!/usr/bin/env python3
"""Check the real cramfs image, including ownership, devices and file contents."""
from pathlib import Path
import struct
import subprocess
import unittest
import zlib

ROOT = Path(__file__).resolve().parent


def entries(image):
    def inode(pos):
        a, b, c = struct.unpack_from('<III', image, pos)
        return dict(mode=a & 65535, uid=a >> 16, size=b & 0xffffff,
                    gid=b >> 24, namebytes=(c & 63) * 4, offset=(c >> 6) * 4)
    result = {}

    def walk(node, prefix):
        pos, end = node['offset'], node['offset'] + node['size']
        while pos < end:
            child = inode(pos)
            name = image[pos+12:pos+12+child['namebytes']].rstrip(b'\0').decode()
            path = prefix + name
            result[path] = child
            if child['mode'] & 0o170000 == 0o040000:
                walk(child, path + '/')
            pos += 12 + child['namebytes']
        if pos != end:
            raise ValueError('Malformed cramfs directory')
    walk(inode(64), '')
    return result


def contents(image, node):
    count = (node['size'] + 4095) // 4096
    start = node['offset'] + count * 4
    result = bytearray()
    for i in range(count):
        end, = struct.unpack_from('<I', image, node['offset'] + i * 4)
        result.extend(zlib.decompress(image[start:end]) if end != start else bytes(4096))
        start = end
    return bytes(result[:node['size']])


class ImageChecks(unittest.TestCase):
    def test_real_image(self):
        path = ROOT/'build/rootfs.cramfs'
        subprocess.run(['fsck.cramfs', str(path)], check=True)
        image = path.read_bytes()
        self.assertEqual(image[:4], b'\x45\x3d\xcd\x28')
        self.assertLessEqual(len(image), 0xf0000)
        items = entries(image)
        for node in items.values():
            self.assertEqual((node['uid'], node['gid']), (0, 0))
        self.assertEqual(items['dev/console']['mode'], 0o020600)
        self.assertEqual(items['dev/console']['size'], 0x0501)
        self.assertEqual(contents(image, items['bin/busybox']),
                         (ROOT.parent/'busybox/build/busybox').read_bytes())
        self.assertEqual(contents(image, items['init']), (ROOT/'init').read_bytes())
        self.assertEqual(contents(image, items['bin/sh']), b'busybox')
        self.assertEqual(contents(image, items['etc/c5-proof']), b'C5FILE:external-cat\n')
        self.assertIn('data', items)

    def test_init(self):
        subprocess.run(['sh', '-n', str(ROOT/'init')], check=True)
        self.assertNotIn('tmpfs /data', (ROOT/'init').read_text())


if __name__ == '__main__':
    unittest.main()
