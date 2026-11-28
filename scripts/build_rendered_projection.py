#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from schema_contract import choose_enum, ensure_required_fields, validate_instance
from render_binding import compute_view_hash, render_markdown
from immutable_receipt import assert_new, write_new
from safe_pack_io import read_bytes, replace_bytes
from validate_pack import check_tree_bound

PROFILE_KIND = "PRIME_CANON_RENDER_PROFILE_V1"
PLAN_KIND = "PRIME_CANON_RENDER_PLAN_V1"
EXEC_KIND = "PRIME_CANON_RENDER_EXECUTION_RECEIPT_V1"
DRIFT_KIND = "PRIME_CANON_RENDER_DRIFT_RECEIPT_V1"
VIEW_KIND = "PRIME_CANON_CURRENT_STATE_VIEW_V1"
NODE_KIND = "PRIME_CANON_NODE_V1"

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def open_rendered_directory(repo: Path) -> int:
    """Open the output directory without following either parent component."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        repo_fd = os.open(repo, flags)
        try:
            views_fd = os.open("views", flags, dir_fd=repo_fd)
            try:
                try:
                    os.mkdir("rendered", dir_fd=views_fd)
                except FileExistsError:
                    pass
                return os.open("rendered", flags, dir_fd=views_fd)
            finally:
                os.close(views_fd)
        finally:
            os.close(repo_fd)
    except OSError as exc:
        raise SystemExit(f"unsafe rendered output directory: {exc}") from exc


def check_rendered_destination(directory_fd: int, filename: str) -> None:
    try:
        mode = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise SystemExit(f"unsafe rendered output destination: {filename} is not a regular file")


def write_rendered_projection(repo: Path, subject_slug: str, rendered_bytes: bytes) -> str:
    filename = f"{subject_slug}.md"
    directory_fd = open_rendered_directory(repo)
    temporary_name = f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        check_rendered_destination(directory_fd, filename)
        fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=directory_fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(rendered_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            # A replacement symlink cannot redirect os.replace, but reject it anyway.
            check_rendered_destination(directory_fd, filename)
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        finally:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory_fd)
    return hashlib.sha256(rendered_bytes).hexdigest()

def load_yaml(repo: Path, path: Path):
    raw = read_bytes(repo, path.relative_to(repo).as_posix(), limit=2 * 1024 * 1024)
    obj = yaml.safe_load(raw)
    check_tree_bound(obj)
    return obj

def main() -> None:
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(".").resolve()
    subject_slug = sys.argv[2] if len(sys.argv) > 2 else "subject-0001"
    generation = sys.argv[3] if len(sys.argv) > 3 else "default"
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", subject_slug):
        raise SystemExit("invalid subject slug")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", generation):
        raise SystemExit("invalid render generation")
    suffix = "" if generation == "default" else f"-{generation}"

    corpus_index_path = repo / "canon" / "corpora" / subject_slug / "index.yaml"
    view_path = repo / "views" / "current" / f"{subject_slug}.yaml"
    node_dir = repo / "canon" / "nodes" / subject_slug
    receipt_dir = repo / "receipts" / "immutable" / subject_slug
    rendered_path = repo / "views" / "rendered" / f"{subject_slug}.md"

    profile_cfg_path = repo / "control" / "render" / "render-profile.default.yaml"
    order_cfg_path = repo / "control" / "render" / "render-order.default.yaml"
    targets_cfg_path = repo / "control" / "render" / "render-targets.default.yaml"
    redaction_cfg_path = repo / "control" / "render" / "render-redaction.default.yaml"

    if not corpus_index_path.exists():
        raise SystemExit(f"missing corpus index: {corpus_index_path}")
    if not view_path.exists():
        raise SystemExit(f"missing current state view: {view_path}")
    if not node_dir.exists():
        raise SystemExit(f"missing node dir: {node_dir}")
    if not receipt_dir.exists():
        raise SystemExit(f"missing receipt dir: {receipt_dir}")

    # Refuse a rerun before changing nodes, the view, or rendered output.
    execution_path = receipt_dir / f"render-execution{suffix}.yaml"
    drift_path = receipt_dir / f"render-drift{suffix}.yaml"
    assert_new(repo, execution_path)
    assert_new(repo, drift_path)

    # Reject unsafe output leaves and parent components before mutating nodes or views.
    rendered_fd = open_rendered_directory(repo)
    try:
        check_rendered_destination(rendered_fd, rendered_path.name)
    finally:
        os.close(rendered_fd)

    corpus = load_yaml(repo, corpus_index_path)
    view = load_yaml(repo, view_path)
    profile_cfg = load_yaml(repo, profile_cfg_path)
    order_cfg = load_yaml(repo, order_cfg_path)
    targets_cfg = load_yaml(repo, targets_cfg_path)
    redaction_cfg = load_yaml(repo, redaction_cfg_path)

    corpus_id = corpus["spec"]["corpus_id"]
    view_id = view["spec"]["view_id"]
    now = utc_now()
    rendered_rel_path = f"views/rendered/{subject_slug}.md"
    execution_receipt_id = f"pc.render_execution_receipt.{subject_slug}.{generation}"
    drift_receipt_id = f"pc.render_drift_receipt.{subject_slug}.{generation}"
    plan_id = f"pc.render_plan.{subject_slug}.{generation}"

    default_output_format = str(profile_cfg["default_output_format"])
    target_projection_flag = str(profile_cfg["target_projection_flag"])
    section_strategy = str(profile_cfg["section_strategy"])
    ordering_strategy = str(profile_cfg["ordering_strategy"])
    heading_style = str(profile_cfg["heading_style"])
    include_metadata_fields = list(profile_cfg.get("include_metadata_fields", []))
    include_receipt_footer = bool(profile_cfg.get("include_receipt_footer", False))
    redaction_policy_ref = str(profile_cfg["redaction_policy_ref"])
    render_order_ref = str(profile_cfg["render_order_ref"])
    render_targets_ref = str(profile_cfg["render_targets_ref"])

    allowed_projection_flags = list(targets_cfg["allowed_projection_flags"])
    default_projection_flags = list(targets_cfg["default_projection_flags"])
    allowed_output_formats = list(targets_cfg["allowed_output_formats"])

    if target_projection_flag not in allowed_projection_flags:
        raise SystemExit(f"target_projection_flag {target_projection_flag} not allowed by render-targets")
    if default_output_format not in allowed_output_formats:
        raise SystemExit(f"default_output_format {default_output_format} not allowed by render-targets")
    if str(order_cfg["ordering_mode"]) != "active_node_refs":
        raise SystemExit("only ordering_mode=active_node_refs is supported in this first cycle-2 render refactor")
    if str(redaction_cfg["policy_mode"]) != "none":
        raise SystemExit("only redaction policy_mode=none is supported in this first cycle-2 render refactor")
    if default_output_format != "markdown":
        raise SystemExit("only markdown output is supported in this first cycle-2 render refactor")
    if target_projection_flag != "rendered_text":
        raise SystemExit("this first cycle-2 render refactor expects target_projection_flag=rendered_text")

    render_profile = {
        "kind": PROFILE_KIND,
        "version": "v1",
        "metadata": {
            "object_id": str(profile_cfg["profile_id"]),
            "display_name": f"{subject_slug} render profile default",
            "created_at": now,
            "updated_at": now,
            "created_by": "compiler:rendered-projection-builder@2.0.0",
            "owners": ["team:prime-canon"],
            "tags": ["prime-canon", subject_slug, "render-profile", "cycle-2"],
        },
        "attributes": {
            "identity": {
                "corpus_id": corpus_id,
                "subject_slug": subject_slug,
                "canonical_home": str(profile_cfg_path.relative_to(repo)),
            },
            "lineage": {
                "source_refs": [],
                "source_unit_refs": [],
                "reduction_path": ["render_profile_validate"],
                "derivation_receipt_refs": [],
            },
            "evidence": {
                "burden_level": "medium",
                "sufficiency_status": "sufficient",
            },
            "governance": {
                "jurisdiction_ref": "pc.jurisdiction.human-reviewer",
                "approval_level": "peer",
                "actor_class": "compiler",
                "risk_tier": "low",
                "promotion_tier": "promoted",
                "freeze_class": "reviewed",
            },
            "semantic": {
                "domain": "prime-canon",
                "scope": "render-profile",
            },
            "relational": {
                "depends_on": [render_order_ref, render_targets_ref, redaction_policy_ref],
                "cluster_refs": [],
                "section_refs": [],
            },
            "operational": {
                "lane_ref": "rendered-projection",
                "repo_home": str(profile_cfg_path.relative_to(repo)),
                "active_state": "active",
                "mirror_permissions": "local_only",
                "route_destination": "render-projection",
            },
            "temporal": {
                "status_since": now,
                "replay_stability": "stable",
                "drift_sensitivity": "low",
                "maturity": "working",
                "reduced_at": now,
            },
        },
        "spec": {
            "profile_id": str(profile_cfg["profile_id"]),
            "default_output_format": default_output_format,
            "target_projection_flag": target_projection_flag,
            "section_strategy": section_strategy,
            "ordering_strategy": ordering_strategy,
            "heading_style": heading_style,
            "include_metadata_fields": include_metadata_fields,
            "include_receipt_footer": include_receipt_footer,
            "redaction_policy_ref": redaction_policy_ref,
            "render_order_ref": render_order_ref,
            "render_targets_ref": render_targets_ref,
        },
    }

    ensure_required_fields(repo, PROFILE_KIND, render_profile["spec"])
    validate_instance(repo, render_profile)

    node_ids = list(view["spec"].get("active_node_refs", []))
    if not node_ids:
        raise SystemExit(f"current state view has no active_node_refs: {view_path}")

    render_plan = {
        "kind": PLAN_KIND,
        "version": "v1",
        "metadata": {
            "object_id": plan_id,
            "display_name": f"{subject_slug} render plan default",
            "created_at": now,
            "updated_at": now,
            "created_by": "compiler:rendered-projection-builder@2.0.0",
            "owners": ["team:prime-canon"],
            "tags": ["prime-canon", subject_slug, "render-plan", "cycle-2"],
        },
        "attributes": {
            "identity": {
                "corpus_id": corpus_id,
                "subject_slug": subject_slug,
                "canonical_home": rendered_rel_path,
            },
            "lineage": {
                "source_refs": [],
                "source_unit_refs": [],
                "reduction_path": ["render_plan_validate"],
                "derivation_receipt_refs": list(view["spec"].get("derivation_receipt_refs", [])),
            },
            "evidence": {
                "burden_level": "medium",
                "sufficiency_status": "sufficient",
            },
            "governance": {
                "jurisdiction_ref": "pc.jurisdiction.human-reviewer",
                "approval_level": "peer",
                "actor_class": "compiler",
                "risk_tier": "low",
                "promotion_tier": "promoted",
                "freeze_class": "reviewed",
            },
            "semantic": {
                "domain": "prime-canon",
                "scope": "render-plan",
            },
            "relational": {
                "depends_on": node_ids + [view_id, str(profile_cfg["profile_id"])],
                "cluster_refs": [],
                "section_refs": [],
            },
            "operational": {
                "lane_ref": "rendered-projection",
                "repo_home": rendered_rel_path,
                "active_state": "active",
                "mirror_permissions": "local_only",
                "route_destination": "render-projection",
            },
            "temporal": {
                "status_since": now,
                "replay_stability": "stable",
                "drift_sensitivity": "low",
                "maturity": "working",
                "reduced_at": now,
            },
        },
        "spec": {
            "plan_id": plan_id,
            "scope_ref": corpus_id,
            "profile_ref": str(profile_cfg["profile_id"]),
            "current_state_view_ref": view_id,
            "target_node_refs": node_ids,
            "output_path": rendered_rel_path,
            "output_format": default_output_format,
            "target_projection_flag": target_projection_flag,
            "ordering_strategy": ordering_strategy,
            "section_strategy": section_strategy,
            "render_order_ref": render_order_ref,
            "redaction_policy_ref": redaction_policy_ref,
            "generated_from_ref": view_id,
        },
    }

    ensure_required_fields(repo, PLAN_KIND, render_plan["spec"])
    validate_instance(repo, render_plan)

    node_objects = []
    all_source_refs = []
    all_source_unit_refs = []

    for node_id in node_ids:
        seq = node_id.rsplit(".", 1)[-1]
        node_path = node_dir / f"node-{seq}.yaml"
        if not node_path.exists():
            raise SystemExit(f"missing node file for {node_id}: {node_path}")
        node = load_yaml(repo, node_path)

        flags = list(node["spec"].get("active_projection_flags", []))
        for item in default_projection_flags:
            if item == target_projection_flag and item not in flags:
                flags.append(item)
        if "machine_readable" not in flags:
            flags.insert(0, "machine_readable")

        ordered = []
        for item in ["machine_readable", "rendered_text", "exportable"]:
            if item in flags and item not in ordered:
                ordered.append(item)

        node["spec"]["active_projection_flags"] = ordered
        node["metadata"]["updated_at"] = now
        node["attributes"]["temporal"]["reduced_at"] = now
        node["attributes"]["temporal"]["status_since"] = now

        ensure_required_fields(repo, NODE_KIND, node["spec"])
        validate_instance(repo, node)
        replace_bytes(repo, node_path.relative_to(repo).as_posix(),
                      yaml.safe_dump(node, sort_keys=False).encode("utf-8"))

        node_objects.append(node)
        all_source_refs.extend(node["attributes"]["lineage"].get("source_refs", []))
        all_source_unit_refs.extend(node["attributes"]["lineage"].get("source_unit_refs", []))

    corpus["metadata"]["updated_at"] = now
    corpus["attributes"]["temporal"]["reduced_at"] = now
    corpus["attributes"]["temporal"]["status_since"] = now
    corpus["attributes"]["operational"]["route_destination"] = "export-prep"
    replace_bytes(repo, corpus_index_path.relative_to(repo).as_posix(),
                  yaml.safe_dump(corpus, sort_keys=False).encode("utf-8"))

    rendered_bytes = render_markdown(subject_slug, corpus_id, view_id, node_objects,
                                     profile_cfg, plan_id, now)
    output_sha256 = write_rendered_projection(repo, subject_slug, rendered_bytes)

    prior_receipts = list(view["spec"].get("derivation_receipt_refs", []))

    execution_receipt = {
        "kind": EXEC_KIND,
        "version": "v1",
        "metadata": {
            "object_id": execution_receipt_id,
            "display_name": f"{subject_slug} render execution receipt",
            "created_at": now,
            "updated_at": now,
            "created_by": "compiler:rendered-projection-builder@2.0.0",
            "owners": ["team:prime-canon"],
            "tags": ["prime-canon", subject_slug, "render-execution", "cycle-2"],
        },
        "attributes": {
            "identity": {
                "corpus_id": corpus_id,
                "subject_slug": subject_slug,
                "canonical_home": str(execution_path.relative_to(repo)),
            },
            "lineage": {
                "source_refs": sorted(set(all_source_refs)),
                "source_unit_refs": sorted(set(all_source_unit_refs)),
                "reduction_path": ["rendered_projection", "render_execution"],
                "derivation_receipt_refs": prior_receipts,
            },
            "evidence": {
                "burden_level": "medium",
                "sufficiency_status": "sufficient",
            },
            "governance": {
                "jurisdiction_ref": "pc.jurisdiction.human-reviewer",
                "approval_level": "peer",
                "actor_class": "compiler",
                "risk_tier": "low",
                "promotion_tier": "promoted",
                "freeze_class": "reviewed",
            },
            "semantic": {
                "domain": "prime-canon",
                "scope": "render-execution",
            },
            "relational": {
                "depends_on": node_ids + [view_id],
                "cluster_refs": [],
                "section_refs": [],
            },
            "operational": {
                "lane_ref": "rendered-projection",
                "repo_home": str(execution_path.relative_to(repo)),
                "active_state": "active",
                "mirror_permissions": "local_only",
                "route_destination": "export-prep",
            },
            "temporal": {
                "status_since": now,
                "replay_stability": "stable",
                "drift_sensitivity": "low",
                "maturity": "working",
                "reduced_at": now,
            },
        },
        "spec": {
            "execution_receipt_id": execution_receipt_id,
            "plan_ref": plan_id,
            "profile_ref": str(profile_cfg["profile_id"]),
            "target_view_ref": view_id,
            "output_ref": rendered_rel_path,
            "output_sha256": output_sha256,
            "rendered_node_count": len(node_objects),
            "rendered_projection_flag": target_projection_flag,
            "execution_status": choose_enum(repo, EXEC_KIND, "execution_status", "success", "partial"),
            "generated_at": now,
            "cited_object_refs": node_ids + [view_id],
            "drift_check_required": True,
        },
    }

    ensure_required_fields(repo, EXEC_KIND, execution_receipt["spec"])
    validate_instance(repo, execution_receipt)

    view["metadata"]["updated_at"] = now
    view["attributes"]["temporal"]["reduced_at"] = now
    view["attributes"]["temporal"]["status_since"] = now
    view["attributes"]["operational"]["route_destination"] = "export-prep"
    view["spec"]["rendered_projection_refs"] = [rendered_rel_path]
    view["spec"]["derivation_receipt_refs"] = prior_receipts + [execution_receipt_id, drift_receipt_id]
    view["spec"]["generated_at"] = now
    view["spec"]["view_hash"] = compute_view_hash(repo, subject_slug, view)

    ensure_required_fields(repo, VIEW_KIND, view["spec"])
    validate_instance(repo, view)
    current_view_hash = view["spec"]["view_hash"]

    drift_receipt = {
        "kind": DRIFT_KIND,
        "version": "v1",
        "metadata": {
            "object_id": drift_receipt_id,
            "display_name": f"{subject_slug} render drift receipt",
            "created_at": now,
            "updated_at": now,
            "created_by": "compiler:rendered-projection-builder@2.0.0",
            "owners": ["team:prime-canon"],
            "tags": ["prime-canon", subject_slug, "render-drift", "cycle-2"],
        },
        "attributes": {
            "identity": {
                "corpus_id": corpus_id,
                "subject_slug": subject_slug,
                "canonical_home": str(drift_path.relative_to(repo)),
            },
            "lineage": {
                "source_refs": sorted(set(all_source_refs)),
                "source_unit_refs": sorted(set(all_source_unit_refs)),
                "reduction_path": ["rendered_projection", "render_drift_check"],
                "derivation_receipt_refs": prior_receipts + [execution_receipt_id],
            },
            "evidence": {
                "burden_level": "medium",
                "sufficiency_status": "sufficient",
            },
            "governance": {
                "jurisdiction_ref": "pc.jurisdiction.human-reviewer",
                "approval_level": "peer",
                "actor_class": "compiler",
                "risk_tier": "low",
                "promotion_tier": "promoted",
                "freeze_class": "reviewed",
            },
            "semantic": {
                "domain": "prime-canon",
                "scope": "render-drift",
            },
            "relational": {
                "depends_on": node_ids + [view_id, execution_receipt_id],
                "cluster_refs": [],
                "section_refs": [],
            },
            "operational": {
                "lane_ref": "rendered-projection",
                "repo_home": str(drift_path.relative_to(repo)),
                "active_state": "active",
                "mirror_permissions": "local_only",
                "route_destination": "export-prep",
            },
            "temporal": {
                "status_since": now,
                "replay_stability": "stable",
                "drift_sensitivity": "low",
                "maturity": "working",
                "reduced_at": now,
            },
        },
        "spec": {
            "drift_receipt_id": drift_receipt_id,
            "plan_ref": plan_id,
            "target_view_ref": view_id,
            "output_ref": rendered_rel_path,
            "current_view_hash": current_view_hash,
            "output_sha256": output_sha256,
            "drift_status": choose_enum(repo, DRIFT_KIND, "drift_status", "in_sync", "stale_view"),
            "compared_at": now,
            "blocker": False,
            "recommended_route": choose_enum(repo, DRIFT_KIND, "recommended_route", "export_prep", "deferred"),
        },
    }

    ensure_required_fields(repo, DRIFT_KIND, drift_receipt["spec"])
    validate_instance(repo, drift_receipt)

    write_new(repo, execution_path, execution_receipt)
    write_new(repo, drift_path, drift_receipt)
    # The current view is the final pointer; a failed append-only receipt write
    # must leave it pointing to the previous generation.
    replace_bytes(repo, view_path.relative_to(repo).as_posix(),
                  yaml.safe_dump(view, sort_keys=False).encode("utf-8"))

    print(render_profile["spec"]["profile_id"])
    print(plan_id)
    print(execution_receipt_id)
    print(drift_receipt_id)
    print(rendered_rel_path)
    print(f"rendered_nodes={len(node_ids)}")

if __name__ == "__main__":
    main()
