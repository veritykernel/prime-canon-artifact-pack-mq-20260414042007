from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "publish-guard-check.yml",
    ROOT / ".github" / "workflows" / "validate-prime-canon.yml",
)
PINNED_USE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
HASH_LOCK = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==[^\s]+ "
    r"--hash=sha256:[0-9a-f]{64}$"
)


class CiIntegrityTests(unittest.TestCase):
    def test_workflows_are_immutable_and_least_privilege(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                source = path.read_text(encoding="utf-8")
                workflow = yaml.safe_load(source)
                self.assertEqual(workflow.get("permissions"), {"contents": "read"})
                self.assertNotIn("pull_request_target:", source)

                jobs = workflow.get("jobs", {})
                self.assertTrue(jobs)
                for job_name, job in jobs.items():
                    with self.subTest(workflow=path.name, job=job_name):
                        self.assertEqual(job.get("runs-on"), "ubuntu-24.04")
                        self.assertNotIn("permissions", job)

                        checkout_seen = False
                        setup_python_seen = False
                        for step in job.get("steps", []):
                            uses = step.get("uses")
                            if uses is not None:
                                self.assertRegex(uses, PINNED_USE)
                            if uses and uses.startswith("actions/checkout@"):
                                checkout_seen = True
                                self.assertIs(step.get("with", {}).get("persist-credentials"), False)
                            if uses and uses.startswith("actions/setup-python@"):
                                setup_python_seen = True
                                self.assertEqual(step.get("with", {}).get("python-version"), "3.11.18")

                        self.assertTrue(checkout_seen)
                        self.assertTrue(setup_python_seen)

    def test_validator_dependency_install_is_binary_and_hash_locked(self):
        workflow = (ROOT / ".github" / "workflows" / "validate-prime-canon.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("--only-binary=:all:", workflow)
        self.assertIn("--require-hashes", workflow)

        lines = [
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(lines)
        locked_names = set()
        for line in lines:
            match = HASH_LOCK.fullmatch(line)
            self.assertIsNotNone(match, f"dependency is not exactly hash locked: {line}")
            locked_names.add(match.group("name").lower().replace("_", "-"))

        self.assertEqual(
            locked_names,
            {
                "attrs",
                "jsonschema",
                "jsonschema-specifications",
                "pyyaml",
                "referencing",
                "rpds-py",
                "typing-extensions",
            },
        )


if __name__ == "__main__":
    unittest.main()
