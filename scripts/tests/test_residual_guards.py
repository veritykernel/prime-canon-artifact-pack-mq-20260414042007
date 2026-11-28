from __future__ import annotations

import shutil
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from immutable_receipt import write_new
from validate_pack import MAX_OBJECT_BYTES, MAX_SCALAR_CHARS, check_tree_bound, load_yaml
from check_render_drift import inspect
from check_publish_guard_trusted import FROZEN_FILES, check_candidate
from object_identity import index_repo
from publish_verified_subject import publish
from safe_pack_io import ensure_directory, write_new_bytes
from scout_contradictions_and_supersession import MAX_TOTAL_INPUT_BYTES, candidate_pairs, read_yaml as read_scout_yaml
from verify_trusted_python import verify as verify_trusted_python
from schema_contract import bundle as schema_bundle
import build_rendered_projection
import yaml

SOURCE = Path(__file__).resolve().parents[2]
SUBJECT = "subject-0001"


class ResidualGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()

    def stage_pack(self):
        for directory in ("canon", "receipts", "views", "control", "schemas"):
            shutil.copytree(SOURCE / directory, self.repo / directory)

    def test_immutable_writer_preserves_existing_receipt(self):
        path = self.repo / "receipt.yaml"
        path.write_text("original")
        with self.assertRaisesRegex(SystemExit, "already exists"):
            write_new(self.repo, path, {"changed": True})
        self.assertEqual(path.read_text(), "original")

    def test_render_builder_refuses_existing_receipts_before_mutating_inputs(self):
        self.stage_pack()
        node = self.repo / "canon" / "nodes" / SUBJECT / "node-0001.yaml"
        view = self.repo / "views" / "current" / f"{SUBJECT}.yaml"
        original = (node.read_bytes(), view.read_bytes())
        result = subprocess.run([sys.executable, str(SOURCE / "scripts" / "build_rendered_projection.py"),
                                 str(self.repo), SUBJECT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual((node.read_bytes(), view.read_bytes()), original)

    def test_topology_scout_refuses_existing_summary_before_mutating_corpus(self):
        self.stage_pack()
        corpus = self.repo / "canon" / "corpora" / SUBJECT / "index.yaml"
        original = corpus.read_bytes()
        result = subprocess.run([sys.executable, str(SOURCE / "scripts" / "scout_contradictions_and_supersession.py"),
                                 str(self.repo), SUBJECT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(corpus.read_bytes(), original)

    def test_topology_scout_writes_one_new_summary(self):
        self.stage_pack()
        summary = self.repo / "receipts" / "immutable" / SUBJECT / "topology-scout-summary.yaml"
        summary.unlink()
        result = subprocess.run([sys.executable, str(SOURCE / "scripts" / "scout_contradictions_and_supersession.py"),
                                 str(self.repo), SUBJECT], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(summary.exists())
        self.assertIn("topology-scout", summary.read_text())

    def test_successor_render_generation_preserves_old_receipts(self):
        self.stage_pack()
        old = self.repo / "receipts" / "immutable" / SUBJECT / "render-execution.yaml"
        original = old.read_bytes()
        result = subprocess.run([sys.executable, str(SOURCE / "scripts" / "build_rendered_projection.py"),
                                 str(self.repo), SUBJECT, "v2"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(old.read_bytes(), original)
        self.assertTrue((old.parent / "render-execution-v2.yaml").exists())
        self.assertEqual(inspect(self.repo, SUBJECT)[1], "in_sync")
        stage = (self.root / "stage").resolve()
        stage.mkdir()
        target = stage / f"{SUBJECT}.rendered.md"
        cfg_path = self.repo / "control" / "publish" / "publish-targets.default.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
        cfg["subjects"][SUBJECT]["rendered_markdown_path"] = str(target)
        cfg_path.write_text(yaml.safe_dump(cfg))
        self.assertEqual(publish(self.repo, SUBJECT, stage_root=stage), target)
        self.assertEqual(target.read_bytes(), (self.repo / "views" / "rendered" / f"{SUBJECT}.md").read_bytes())

    def test_failed_drift_receipt_write_does_not_advance_current_view(self):
        self.stage_pack()
        view = self.repo / "views" / "current" / f"{SUBJECT}.yaml"
        original_view = view.read_bytes()
        original_writer = build_rendered_projection.write_new

        def fail_drift(repo, path, obj):
            if path.name == "render-drift-v2.yaml":
                raise OSError("injected drift receipt failure")
            return original_writer(repo, path, obj)

        with patch.object(build_rendered_projection, "write_new", side_effect=fail_drift), \
             patch.object(sys, "argv", ["render", str(self.repo), SUBJECT, "v2"]), \
             self.assertRaisesRegex(OSError, "injected drift receipt failure"):
            build_rendered_projection.main()
        self.assertEqual(view.read_bytes(), original_view)

    def test_render_builder_rejects_live_output_symlink_before_other_writes(self):
        self.stage_pack()
        output = self.repo / "views" / "rendered" / f"{SUBJECT}.md"
        sentinel = self.root / "outside.md"
        sentinel.write_text("KEEP", encoding="utf-8")
        output.unlink()
        output.symlink_to(sentinel)
        node = self.repo / "canon" / "nodes" / SUBJECT / "node-0001.yaml"
        view = self.repo / "views" / "current" / f"{SUBJECT}.yaml"
        original = (node.read_bytes(), view.read_bytes())

        result = subprocess.run(
            [sys.executable, str(SOURCE / "scripts" / "build_rendered_projection.py"),
             str(self.repo), SUBJECT, "v2"],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe rendered output destination", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "KEEP")
        self.assertEqual((node.read_bytes(), view.read_bytes()), original)

    def test_render_builder_rejects_dangling_output_symlink_before_other_writes(self):
        self.stage_pack()
        output = self.repo / "views" / "rendered" / f"{SUBJECT}.md"
        outside = self.root / "not-created.md"
        output.unlink()
        output.symlink_to(outside)
        node = self.repo / "canon" / "nodes" / SUBJECT / "node-0001.yaml"
        view = self.repo / "views" / "current" / f"{SUBJECT}.yaml"
        original = (node.read_bytes(), view.read_bytes())

        result = subprocess.run(
            [sys.executable, str(SOURCE / "scripts" / "build_rendered_projection.py"),
             str(self.repo), SUBJECT, "v2"],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe rendered output destination", result.stderr)
        self.assertFalse(outside.exists())
        self.assertEqual((node.read_bytes(), view.read_bytes()), original)

    def test_validator_rejects_oversized_yaml(self):
        path = self.repo / "oversized.yaml"
        path.write_bytes(b"a" * (MAX_OBJECT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "bounded regular"):
            load_yaml(path)

    def test_validator_rejects_large_scalar_within_file_budget(self):
        with self.assertRaisesRegex(ValueError, "scalar exceeds size limit"):
            check_tree_bound({"payload": "x" * (MAX_SCALAR_CHARS + 1)})

    def test_yaml_fifo_rejected_without_waiting_for_writer(self):
        fifo = self.repo / "blocked.yaml"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "bounded regular"):
            load_yaml(fifo)

    def test_scout_aggregate_byte_budget_holds(self):
        sample = self.repo / "sample.yaml"
        sample.write_text("a: b\n")
        with self.assertRaisesRegex(SystemExit, "aggregate byte budget"):
            read_scout_yaml(self.repo, sample, [MAX_TOTAL_INPUT_BYTES - 1])

    def test_dense_scout_candidate_set_fails_bounded(self):
        claims = [{"attributes": {"semantic": {"scope": "same"}}} for _ in range(182)]
        with self.assertRaisesRegex(ValueError, "candidate pairs exceed"):
            candidate_pairs(claims, [set() for _ in claims])

    def test_repository_wide_duplicate_object_id_fails(self):
        self.stage_pack()
        first = self.repo / "canon" / "candidate_claims" / SUBJECT / "claim-0001.yaml"
        second = self.repo / "canon" / "candidate_claims" / SUBJECT / "claim-0002.yaml"
        copied = yaml.safe_load(second.read_text())
        copied["metadata"]["object_id"] = yaml.safe_load(first.read_text())["metadata"]["object_id"]
        second.write_text(yaml.safe_dump(copied, sort_keys=False))
        with self.assertRaisesRegex(ValueError, "duplicate object ID"):
            index_repo(self.repo)

    def test_descriptor_writer_rejects_leaf_and_ancestor_symlinks(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "index.yaml"
        sentinel.write_text("KEEP")
        (self.repo / "canon").mkdir()
        (self.repo / "canon" / "corpora").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            ensure_directory(self.repo, "canon/corpora/new-subject")
        (self.repo / "canon" / "corpora").unlink()
        (self.repo / "canon" / "corpora").mkdir()
        (self.repo / "canon" / "corpora" / "index.yaml").symlink_to(sentinel)
        with self.assertRaises(FileExistsError):
            write_new_bytes(self.repo, "canon/corpora/index.yaml", b"overwrite")
        self.assertEqual(sentinel.read_text(), "KEEP")

    def test_candidate_builder_cannot_write_through_claim_directory_link(self):
        self.stage_pack()
        claim_dir = self.repo / "canon" / "candidate_claims" / SUBJECT
        shutil.rmtree(claim_dir)
        outside = self.root / "outside-claims"
        outside.mkdir()
        claim_dir.symlink_to(outside, target_is_directory=True)
        result = subprocess.run([sys.executable, str(SOURCE / "scripts" / "extract_candidate_claims.py"),
                                 str(self.repo), SUBJECT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_schema_bundle_rejects_symlinked_repository_schema(self):
        self.stage_pack()
        schema = self.repo / "schemas" / "prime-canon-bundle.schema.json"
        outside = self.root / "outside-schema.json"
        outside.write_text("{}")
        schema.unlink()
        schema.symlink_to(outside)
        with self.assertRaises(OSError):
            schema_bundle(self.repo)

    def test_candidate_venv_cannot_run_before_publish_guard(self):
        scripts = self.repo / "scripts"
        scripts.mkdir()
        shutil.copy2(SOURCE / "scripts" / "publish_guard.sh", scripts / "publish_guard.sh")
        (self.repo / ".venv" / "bin").mkdir(parents=True)
        marker = self.root / "venv-executed"
        (self.repo / ".venv" / "bin" / "activate").write_text(f"touch '{marker}'\n")
        environment = dict(os.environ)
        environment.pop("PRIME_CANON_TRUSTED_PYTHON", None)
        result = subprocess.run(["bash", str(scripts / "publish_guard.sh"), "--", "/usr/bin/true"],
                                cwd=self.repo, capture_output=True, text=True, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRIME_CANON_TRUSTED_PYTHON", result.stderr)
        self.assertFalse(marker.exists())
        with self.assertRaisesRegex(ValueError, "outside this repository"):
            verify_trusted_python(self.repo, self.repo.resolve() / ".venv" / "bin" / "python3")

    def test_isolated_publish_import_ignores_candidate_yaml_shadow(self):
        scripts = self.repo / "scripts"
        scripts.mkdir()
        marker = self.root / "yaml-shadow-imported"
        (scripts / "yaml.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n")
        code = ("import sys\nimport yaml, jsonschema\n"
                "sys.path.append(sys.argv[1])\nimport yaml\n"
                "assert not yaml.__file__.startswith(sys.argv[1])\n")
        result = subprocess.run([sys.executable, "-I", "-c", code, str(scripts)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_protected_base_guard_blocks_source_and_immutable_tampering(self):
        base = self.root / "base"
        candidate = self.root / "candidate"
        for family in ("scripts", ".github/workflows", "receipts/immutable", "canon/nodes", "views/current"):
            (base / family).mkdir(parents=True)
        (base / "scripts" / "trusted.py").write_text("trusted\n")
        (base / "receipts" / "immutable" / "old.yaml").write_text("old\n")
        for relative in FROZEN_FILES:
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("policy\n")
        shutil.copytree(base, candidate)
        self.assertEqual(check_candidate(base, candidate), [])
        (candidate / "receipts" / "immutable" / "new.yaml").write_text("new\n")
        self.assertEqual(check_candidate(base, candidate), [])
        (candidate / "scripts" / "trusted.py").write_text("candidate bypass\n")
        (candidate / "receipts" / "immutable" / "old.yaml").write_text("changed\n")
        (candidate / "canon" / "nodes" / "new.yaml").write_text("new node\n")
        (candidate / ".venv").mkdir()
        issues = check_candidate(base, candidate)
        self.assertTrue(any("trusted.py" in issue for issue in issues))
        self.assertTrue(any("old.yaml" in issue for issue in issues))
        self.assertTrue(any("new governed objects" in issue for issue in issues))
        self.assertTrue(any(".venv" in issue for issue in issues))

    def test_history_gate_rejects_changed_receipt(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, check=True)
        receipt = self.repo / "receipts" / "immutable" / SUBJECT / "receipt.yaml"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("original")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.repo, check=True)
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        receipt.write_text("replaced")
        subprocess.run(["git", "commit", "-qam", "replace"], cwd=self.repo, check=True)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        result = subprocess.run([sys.executable, str(SOURCE / "scripts" / "check_immutable_history.py"), base, head],
                                cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("receipt.yaml", result.stderr)

    def test_bootstrap_rejects_traversal_before_repo_write(self):
        outside = SOURCE.parent / "codex-bootstrap-traversal-probe"
        self.assertFalse(outside.exists())
        result = subprocess.run(["bash", str(SOURCE / "scripts" / "bootstrap_subject.sh"), "../codex-bootstrap-traversal-probe"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SUBJECT_SLUG", result.stderr)
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
