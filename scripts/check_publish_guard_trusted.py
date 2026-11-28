#!/usr/bin/env python3
"""Read a PR candidate with protected-base code; never execute candidate code."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


FROZEN_DIRS = ("scripts", ".github/workflows")
FROZEN_FILES = (
    ".github/CODEOWNERS",
    "schemas/prime-canon-bundle.schema.json",
    "control/publish/publish-targets.default.yaml",
    "control/render/render-profile.default.yaml",
    "control/render/render-order.default.yaml",
    "control/render/render-targets.default.yaml",
    "control/render/render-redaction.default.yaml",
    "requirements.txt",
    "requirements-ci-linux-x86_64.txt",
)
IMMUTABLE_OLD = ("receipts/immutable", "canon/nodes", "views/current")
NO_NEW = ("canon/nodes", "views/current")
MAX_FILE = 2 * 1024 * 1024


def _regular_bytes(root: Path, relative: str) -> bytes:
    parts = relative.split("/")
    if not parts or any(part in ("", ".", "..") or "\\" in part for part in parts):
        raise ValueError(f"unsafe path: {relative}")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        source_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source_fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                raise ValueError(f"not a bounded regular file: {relative}")
            raw = source.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE:
                raise ValueError(f"oversized file: {relative}")
            return raw
    finally:
        os.close(fd)


def _members(root: Path, family: str) -> set[str]:
    directory = root / family
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"unsafe or missing directory: {family}")
    members = set()
    for path in directory.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        members.add(path.relative_to(root).as_posix())
    return members


def check_candidate(base: Path, candidate: Path) -> list[str]:
    issues = []
    if os.path.lexists(candidate / ".venv"):
        issues.append(".venv: repository-controlled interpreter or activation material is forbidden")
    for family in FROZEN_DIRS + IMMUTABLE_OLD:
        try:
            old, new = _members(base, family), _members(candidate, family)
            if family in FROZEN_DIRS and old != new:
                issues.append(f"{family}: enforcement file set differs from protected base")
            if family in NO_NEW and new - old:
                issues.append(f"{family}: new governed objects need a separately qualified signed admission route")
            for relative in sorted(old):
                try:
                    if _regular_bytes(base, relative) != _regular_bytes(candidate, relative):
                        issues.append(f"{relative}: prior protected object changed")
                except (OSError, ValueError) as exc:
                    issues.append(f"{relative}: unsafe or missing prior object: {exc}")
            for relative in sorted(new - old):
                try:
                    _regular_bytes(candidate, relative)
                except (OSError, ValueError) as exc:
                    issues.append(f"{relative}: unsafe new object: {exc}")
        except (OSError, ValueError) as exc:
            issues.append(f"{family}: {exc}")
    for relative in FROZEN_FILES:
        try:
            if _regular_bytes(base, relative) != _regular_bytes(candidate, relative):
                issues.append(f"{relative}: policy differs from protected base")
        except (OSError, ValueError) as exc:
            issues.append(f"{relative}: unsafe or missing policy: {exc}")
    return issues


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_publish_guard_trusted.py BASE_CHECKOUT CANDIDATE_CHECKOUT")
    base, candidate = (Path(item).resolve(strict=True) for item in sys.argv[1:])
    if base == candidate:
        raise SystemExit("base and candidate must be distinct checkouts")
    issues = check_candidate(base, candidate)
    if issues:
        print("protected-base guard rejected candidate:", file=sys.stderr)
        for issue in issues:
            print(issue, file=sys.stderr)
        raise SystemExit(1)
    print("protected-base guard passed")


if __name__ == "__main__":
    main()
