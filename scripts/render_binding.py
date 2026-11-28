"""Bind a rendered view to the exact node, view, and render-policy bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import yaml

from safe_pack_io import read_bytes


POLICY_PATHS = (
    "control/render/render-profile.default.yaml",
    "control/render/render-order.default.yaml",
    "control/render/render-targets.default.yaml",
    "control/render/render-redaction.default.yaml",
)
MAX_INPUT_BYTES = 2 * 1024 * 1024


def render_snapshot(repo: Path, subject_slug: str, view: dict) -> tuple[str, dict[str, bytes]]:
    """Hash and retain one coherent set of source bytes for verification."""
    repo = repo.resolve(strict=True)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", subject_slug):
        raise ValueError("invalid render subject slug")
    spec = dict(view["spec"])
    spec.pop("view_hash", None)
    node_refs = list(spec.get("active_node_refs", []))
    if len(node_refs) != len(set(node_refs)):
        raise ValueError("duplicate active node reference")
    inputs = []
    raw_by_path = {}
    for node_ref in node_refs:
        match = re.fullmatch(rf"pc\.canon_node\.{re.escape(subject_slug)}\.(\d{{4}})", node_ref)
        if not match:
            raise ValueError(f"invalid active node reference: {node_ref}")
        relative = f"canon/nodes/{subject_slug}/node-{match.group(1)}.yaml"
        raw = read_bytes(repo, relative, limit=MAX_INPUT_BYTES)
        raw_by_path[relative] = raw
        inputs.append({"ref": node_ref, "path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    corpus_relative = f"canon/corpora/{subject_slug}/index.yaml"
    corpus_raw = read_bytes(repo, corpus_relative, limit=MAX_INPUT_BYTES)
    raw_by_path[corpus_relative] = corpus_raw
    inputs.append({"path": corpus_relative, "sha256": hashlib.sha256(corpus_raw).hexdigest()})
    for relative in POLICY_PATHS:
        raw = read_bytes(repo, relative, limit=MAX_INPUT_BYTES)
        raw_by_path[relative] = raw
        inputs.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    payload = {"binding_version": 2, "view_spec": spec, "inputs": inputs}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), raw_by_path


def compute_view_hash(repo: Path, subject_slug: str, view: dict) -> str:
    return render_snapshot(repo, subject_slug, view)[0]


def render_markdown(subject_slug: str, corpus_id: str, view_id: str, node_objects: list[dict],
                    profile_cfg: dict, plan_id: str, generated_at: str) -> bytes:
    """Build the one supported Markdown body from admitted render inputs."""
    if profile_cfg.get("default_output_format") != "markdown" or profile_cfg.get("target_projection_flag") != "rendered_text":
        raise ValueError("unsupported render format or target")
    if profile_cfg.get("section_strategy") != "linear_nodes" or profile_cfg.get("ordering_strategy") != "active_node_order":
        raise ValueError("unsupported render ordering")
    heading_style = profile_cfg.get("heading_style")
    lines = [f"# {subject_slug} rendered canon", ""]
    metadata_map = {"corpus_id": corpus_id, "current_state_view": view_id, "generated_at": generated_at}
    for field in profile_cfg.get("include_metadata_fields", []):
        if field in metadata_map:
            lines.append(f"- {field}: `{metadata_map[field]}`")
    lines.extend((f"- active_node_count: `{len(node_objects)}`", "", "## Promoted canon nodes", ""))
    for idx, node in enumerate(node_objects, start=1):
        body = node["spec"]["canonical_body"]
        lines.append(f"{idx}. {body}" if heading_style == "numbered" else f"- {body}")
    if profile_cfg.get("include_receipt_footer", False):
        lines.extend(("", "## Projection receipts", "",
                      f"- render_profile_ref: `{profile_cfg['profile_id']}`",
                      f"- render_plan_ref: `{plan_id}`"))
    return ("\n".join(lines) + "\n").encode("utf-8")


def expected_render_bytes(subject_slug: str, view: dict, execution: dict,
                          raw_by_path: dict[str, bytes], corpus_id: str) -> bytes:
    node_objects = []
    for node_ref in view["spec"].get("active_node_refs", []):
        seq = node_ref.rsplit(".", 1)[-1]
        relative = f"canon/nodes/{subject_slug}/node-{seq}.yaml"
        node = yaml.safe_load(raw_by_path[relative])
        if node["metadata"]["object_id"] != node_ref:
            raise ValueError(f"node ID does not match rendered path: {relative}")
        node_objects.append(node)
    profile = yaml.safe_load(raw_by_path[POLICY_PATHS[0]])
    return render_markdown(subject_slug, corpus_id, view["spec"]["view_id"], node_objects,
                           profile, execution["spec"]["plan_ref"], execution["metadata"]["created_at"])
