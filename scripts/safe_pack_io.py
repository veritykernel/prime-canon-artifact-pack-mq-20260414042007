"""Descriptor-relative access to one selected repository tree.

The caller selects the repository root before invoking these helpers. Every
component below it is opened without following links, so a swapped ancestor
cannot redirect a later read or write outside that root.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _parts(relative: str) -> tuple[str, ...]:
    path = Path(relative)
    parts = path.parts
    if not parts or path.is_absolute() or any(part in ("", ".", "..") or "\\" in part for part in parts):
        raise ValueError(f"unsafe repository-relative path: {relative}")
    return parts


def open_parent(repo: Path, relative: str, *, create: bool = False) -> tuple[int, str]:
    parts = _parts(relative)
    fd = os.open(repo, DIRECTORY_FLAGS)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def ensure_directory(repo: Path, relative: str) -> None:
    parent, leaf = open_parent(repo, relative, create=True)
    try:
        try:
            os.mkdir(leaf, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
        fd = os.open(leaf, DIRECTORY_FLAGS, dir_fd=parent)
        os.close(fd)
    finally:
        os.close(parent)


def read_bytes(repo: Path, relative: str, *, limit: int) -> bytes:
    parent, leaf = open_parent(repo, relative)
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError(f"input is not a bounded regular file: {relative}")
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError(f"input exceeds size limit: {relative}")
            return raw
    finally:
        os.close(parent)


def write_new_bytes(repo: Path, relative: str, payload: bytes, *, create_parents: bool = False) -> None:
    parent, leaf = open_parent(repo, relative, create=create_parents)
    try:
        fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(parent)


def replace_bytes(repo: Path, relative: str, payload: bytes) -> None:
    parent, leaf = open_parent(repo, relative)
    temporary = f".{leaf}.{secrets.token_hex(12)}.tmp"
    created = False
    try:
        try:
            info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"output is not a regular file: {relative}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
        created = False
    finally:
        if created:
            os.unlink(temporary, dir_fd=parent)
        os.close(parent)
