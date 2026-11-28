#!/usr/bin/env python3
"""Qualify an operator-selected interpreter before guarded publication."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def verify(repo: Path, selected: Path) -> None:
    if not selected.is_absolute() or ".." in selected.parts or selected.is_relative_to(repo.resolve(strict=True)):
        raise ValueError("trusted Python must be an absolute path outside this repository")
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in selected.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
            info = os.fstat(parent)
            if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
                raise ValueError("trusted Python ancestor has unsafe owner or permissions")
        fd = os.open(selected.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
                    or info.st_mode & 0o022 or not info.st_mode & 0o111):
                raise ValueError("trusted Python is not a protected executable file")
    finally:
        os.close(parent)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_trusted_python.py REPO ABSOLUTE_INTERPRETER")
    try:
        verify(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"trusted Python rejected: {exc}") from exc
