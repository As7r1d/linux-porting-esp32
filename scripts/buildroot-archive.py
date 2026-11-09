#!/usr/bin/env python3
"""Extract source regular files first, inert filesystem symlinks last."""
from pathlib import Path, PurePosixPath
import os
import tarfile


def unpack(archive, destination, root_name):
    destination = Path(destination)
    with tarfile.open(archive, 'r:*') as tar:
        members = tar.getmembers()
        if sum(m.size for m in members) > 512 * 1024 * 1024:
            raise ValueError('Unexpected archive expansion size')
        entries = {}
        for member in members:
            path = PurePosixPath(member.name)
            if (not path.parts or path.parts[0] != root_name or '..' in path.parts
                    or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                raise ValueError('Unexpected archive path/type: ' + member.name)
            if path in entries and not (member.isdir() and entries[path].isdir()):
                raise ValueError('Duplicate archive path: ' + member.name)
            entries[path] = member
        links = {path for path, member in entries.items() if member.issym() or member.islnk()}
        for path, member in entries.items():
            if any(parent in links for parent in path.parents):
                raise ValueError('Archive member descends through a link: ' + member.name)
            if member.islnk():
                target = PurePosixPath(member.linkname)
                if target not in entries or not entries[target].isfile():
                    raise ValueError('Hard link must target a regular archive file: ' + member.name)
        # data_filter still protects ordinary paths/types/modes. No links exist
        # during this phase, so neither absolute nor relative links are followed.
        tar.extractall(destination, members=[m for m in members if m.isfile() or m.isdir()], filter='data')
        for path, member in entries.items():
            if member.islnk():
                output = destination.joinpath(*path.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                os.link(destination.joinpath(*PurePosixPath(member.linkname).parts), output)
        for path, member in entries.items():
            if member.issym():
                output = destination.joinpath(*path.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(member.linkname, output)
        return {str(path): m.linkname for path, m in entries.items() if m.issym()}
