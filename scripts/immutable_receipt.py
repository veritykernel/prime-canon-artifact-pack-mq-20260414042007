"""Create immutable receipts once; never silently replace prior evidence."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from safe_pack_io import open_parent, write_new_bytes


def assert_new(repo: Path, path: Path) -> None:
    relative = path.relative_to(repo).as_posix()
    repo = repo.resolve(strict=True)
    try:
        parent, leaf = open_parent(repo, relative)
        try:
            try:
                os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise SystemExit(f"immutable receipt already exists; create a successor generation: {path}")
        finally:
            os.close(parent)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"immutable receipt parent is unsafe: {path}: {exc}") from exc


def write_new(repo: Path, path: Path, obj: dict) -> None:
    assert_new(repo, path)
    payload = yaml.safe_dump(obj, sort_keys=False).encode("utf-8")
    try:
        write_new_bytes(repo, path.relative_to(repo).as_posix(), payload)
    except FileExistsError as exc:
        raise SystemExit(f"immutable receipt already exists; create a successor generation: {path}") from exc
