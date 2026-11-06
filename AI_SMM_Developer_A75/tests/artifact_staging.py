"""Durable derived-artifact projection, independent of Qt or a model.

SQLite is the authority. This module stages a set of exported files on the
same filesystem, verifies staged bytes, and publishes each by atomic rename.
A batch cannot have a cross-file atomic rename: a crash can publish a prefix.
The caller MUST keep its durable export intent pending until all readbacks pass
and replay the complete projection idempotently after restart.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from contextlib import AbstractContextManager

from project_file_boundary import require_managed_file


class StagedArtifactBatch(AbstractContextManager):
    def __init__(self, root: Path, allowed_folders: tuple[str, ...]):
        self.root = Path(root)
        self.allowed_folders = allowed_folders
        self._pairs: list[tuple[Path, Path]] = []
        self._committed = False

    def stage(self, destination: Path) -> Path:
        if self._committed:
            raise RuntimeError('Already published')
        target = require_managed_file(self.root, destination, self.allowed_folders)
        if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
            raise ValueError('Export target is a filesystem link')
        if target.exists() and not target.is_file():
            raise ValueError('Export target is not a regular file')
        if any(target == previous for _, previous in self._pairs):
            raise ValueError('Duplicate staged destination')
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.'+target.stem+'.stage-',
                                   suffix=target.suffix, dir=target.parent)
        os.close(fd)
        staged = Path(name)
        self._pairs.append((staged, target))
        return staged

    def publish(self, verify):
        """Publish only a fully validated batch; verify(stage, target) raises.

        If a later rename fails, the durable caller retries the complete batch;
        this service never pretends to have committed an all-or-nothing group.
        """
        if self._committed or not self._pairs:
            raise RuntimeError('Empty or previously published export')
        for staged, target in self._pairs:
            if not staged.is_file() or staged.is_symlink():
                raise RuntimeError('Staged artifact missing or replaced by a link')
            verify(staged, target)
        for staged, target in self._pairs:
            require_managed_file(self.root, target, self.allowed_folders)
            if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
                raise RuntimeError('Target replaced by a link during export')
            with staged.open('r+b') as handle:
                os.fsync(handle.fileno())
            os.replace(staged, target)
            if os.name == 'posix':
                fd = os.open(target.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        self._committed = True
        for _, target in self._pairs:
            verify(target, target)

    def __exit__(self, exc_type, exc, tb):
        for staged, _ in self._pairs:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass
        return False
