#!/usr/bin/env python3
"""Read-only verification of frozen render receipts against current inputs."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml

from render_binding import expected_render_bytes, render_snapshot
from render_receipts import active_receipt_paths
from safe_pack_io import read_bytes

MAX_RENDER_BYTES = 16 * 1024 * 1024


def load_yaml(repo: Path, relative: str) -> dict:
    obj = yaml.safe_load(read_bytes(repo, relative, limit=2 * 1024 * 1024))
    if not isinstance(obj, dict):
        raise ValueError(f"render input is not an object: {relative}")
    return obj


def current_output_bytes(repo: Path, relative: str) -> bytes | None:
    try:
        return read_bytes(repo, relative, limit=MAX_RENDER_BYTES)
    except FileNotFoundError:
        return None


def inspect(repo: Path, subject_slug: str) -> tuple[str, str]:
    repo = repo.resolve(strict=True)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", subject_slug):
        raise ValueError("invalid subject slug")
    view = load_yaml(repo, f"views/current/{subject_slug}.yaml")
    execution_path, drift_path = active_receipt_paths(repo, subject_slug, view)
    execution = load_yaml(repo, execution_path.relative_to(repo).as_posix())
    drift = load_yaml(repo, drift_path.relative_to(repo).as_posix())
    view_spec, execution_spec, drift_spec = view["spec"], execution["spec"], drift["spec"]
    expected_ref = f"views/rendered/{subject_slug}.md"
    output = current_output_bytes(repo, expected_ref)
    output_sha = hashlib.sha256(output).hexdigest() if output is not None else None
    current_view_hash, render_inputs = render_snapshot(repo, subject_slug, view)
    corpus = yaml.safe_load(render_inputs[f"canon/corpora/{subject_slug}/index.yaml"])
    expected_output = expected_render_bytes(subject_slug, view, execution, render_inputs, corpus["spec"]["corpus_id"])

    if output_sha is None:
        status = "missing_output"
    elif output_sha != execution_spec.get("output_sha256"):
        status = "out_of_sync"
    elif output != expected_output:
        status = "out_of_sync"
    elif (current_view_hash != view_spec.get("view_hash")
          or current_view_hash != drift_spec.get("current_view_hash")
          or output_sha != drift_spec.get("output_sha256")
          or execution_spec.get("output_ref") != expected_ref
          or drift_spec.get("output_ref") != expected_ref
          or execution_spec.get("target_view_ref") != view_spec.get("view_id")
          or drift_spec.get("target_view_ref") != view_spec.get("view_id")
          or drift_spec.get("plan_ref") != execution_spec.get("plan_ref")
          or drift_spec.get("drift_status") != "in_sync"
          or drift_spec.get("blocker") is not False
          or drift_spec.get("recommended_route") != "export_prep"):
        status = "stale_view"
    else:
        status = "in_sync"
    return drift["metadata"]["object_id"], status


def main() -> None:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    subject_slug = sys.argv[2] if len(sys.argv) > 2 else "subject-0001"
    try:
        receipt_id, status = inspect(repo, subject_slug)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise SystemExit(f"render drift check rejected: {exc}") from exc
    blocker = status != "in_sync"
    route = "export_prep" if not blocker else "render_refresh"
    print(receipt_id)
    print(f"drift_status={status}")
    print(f"blocker={str(blocker).lower()}")
    print(f"recommended_route={route}")
    if blocker:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
