#!/usr/bin/env python3
"""Publish exactly the rendered bytes verified against the frozen receipts."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import sys
from pathlib import Path

import yaml

from promotion_preflight import PromotionRejected, SUBJECT
from render_binding import expected_render_bytes, render_snapshot
from render_receipts import active_receipt_paths
from safe_pack_io import read_bytes

MAX_RENDER = 16 * 1024 * 1024
MAX_YAML = 2 * 1024 * 1024
DEFAULT_STAGE_ROOT = Path("/Users/Shared/nuum-work/stage")


def _yaml(repo: Path, relative: str) -> dict:
    obj = yaml.safe_load(read_bytes(repo, relative, limit=MAX_YAML))
    if not isinstance(obj, dict):
        raise PromotionRejected(f"publication input is not an object: {relative}")
    return obj


def _trusted_stage_fd(stage_root: Path) -> int:
    if not stage_root.is_absolute():
        raise PromotionRejected("stage root must be absolute")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = stage_root.parts[1:]
        for index, part in enumerate(parts):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            writable_by_others = bool(info.st_mode & 0o022)
            root_sticky_ancestor = index < len(parts) - 1 and info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in (0, os.geteuid()) or (writable_by_others and not root_sticky_ancestor):
                raise PromotionRejected(f"stage directory has unsafe owner or permissions: {stage_root}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def publish(repo: Path, subject: str, *, stage_root: Path = DEFAULT_STAGE_ROOT) -> Path:
    repo = repo.resolve()
    if not SUBJECT.fullmatch(subject):
        raise PromotionRejected("invalid subject slug")
    cfg = _yaml(repo, "control/publish/publish-targets.default.yaml")
    view = _yaml(repo, f"views/current/{subject}.yaml")
    try:
        execution_path, drift_path = active_receipt_paths(repo, subject, view)
    except (ValueError, KeyError, TypeError) as exc:
        raise PromotionRejected(f"current render receipt refs are invalid: {exc}") from exc
    execution = _yaml(repo, execution_path.relative_to(repo).as_posix())
    drift = _yaml(repo, drift_path.relative_to(repo).as_posix())
    expected_ref = f"views/rendered/{subject}.md"
    spec = execution["spec"]
    drift_spec = drift["spec"]
    view_spec = view["spec"]
    try:
        actual_view_hash, render_inputs = render_snapshot(repo, subject, view)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PromotionRejected(f"current render inputs could not be verified: {exc}") from exc
    corpus = yaml.safe_load(render_inputs[f"canon/corpora/{subject}/index.yaml"])
    if (spec.get("output_ref") != expected_ref or drift_spec.get("output_ref") != expected_ref
            or spec.get("target_view_ref") != view_spec.get("view_id")
            or drift_spec.get("target_view_ref") != view_spec.get("view_id")
            or drift_spec.get("current_view_hash") != view_spec.get("view_hash")
            or actual_view_hash != view_spec.get("view_hash")
            or drift_spec.get("output_sha256") != spec.get("output_sha256")
            or drift_spec.get("plan_ref") != spec.get("plan_ref")
            or drift_spec.get("drift_status") != "in_sync"
            or drift_spec.get("blocker") is not False
            or drift_spec.get("recommended_route") != "export_prep"):
        raise PromotionRejected("render receipts and current view do not authorize these exact bytes")
    raw = read_bytes(repo, expected_ref, limit=MAX_RENDER)
    try:
        expected = expected_render_bytes(subject, view, execution, render_inputs, corpus["spec"]["corpus_id"])
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise PromotionRejected(f"could not derive expected render body: {exc}") from exc
    if raw != expected or hashlib.sha256(raw).hexdigest() != spec.get("output_sha256"):
        raise PromotionRejected("rendered source bytes differ from governed source and execution receipt")
    target_value = cfg.get("subjects", {}).get(subject, {}).get("rendered_markdown_path")
    if not isinstance(target_value, str) or not target_value:
        raise PromotionRejected("missing publish target")
    target = stage_root / f"{subject}.rendered.md"
    if target_value != str(target):
        raise PromotionRejected("publish target differs from the trusted stage destination")
    directory_fd = _trusted_stage_fd(stage_root)
    try:
        try:
            old = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(old.st_mode):
                raise PromotionRejected("existing publish target is not a regular file")
        temp_name = f".{subject}.rendered.{secrets.token_hex(12)}.tmp"
        temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                          0o644, dir_fd=directory_fd)
        try:
            with os.fdopen(temp_fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            verify_fd = os.open(temp_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            with os.fdopen(verify_fd, "rb") as stream:
                if hashlib.sha256(stream.read(MAX_RENDER + 1)).hexdigest() != hashlib.sha256(raw).hexdigest():
                    raise PromotionRejected("stage temporary bytes failed digest readback")
            # Both pathnames are relative to the same already-open directory.
            # Replace never follows a destination symlink introduced by a race.
            os.replace(temp_name, target.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        finally:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory_fd)
    return target


if __name__ == "__main__":
    try:
        destination = publish(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("."),
                              sys.argv[2] if len(sys.argv) > 2 else "subject-0001")
    except (PromotionRejected, OSError) as exc:
        raise SystemExit(f"publication rejected: {exc}") from exc
    print(f"published_to={destination}")
