"""Repository-wide identity index for governed canon objects."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from safe_pack_io import read_bytes


ROOTS = ("control", "canon", "receipts", "replay", "views")
MAX_OBJECTS = 5000
MAX_OBJECT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
CLAIM_PATH = re.compile(r"canon/candidate_claims/([a-z0-9]+(?:-[a-z0-9]+)*)/claim-([0-9]{4})\.yaml\Z")


class ObjectIndex:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, str]] = {}

    def add(self, relative: str, data: dict, raw: bytes) -> None:
        if "kind" not in data:
            return
        metadata = data.get("metadata")
        identity = data.get("attributes", {}).get("identity", {})
        object_id = metadata.get("object_id") if isinstance(metadata, dict) else None
        if not isinstance(object_id, str) or not object_id:
            raise ValueError(f"governed object lacks an ID: {relative}")
        if identity.get("canonical_home") != relative:
            raise ValueError(f"canonical_home mismatch for {object_id}: {relative}")
        if object_id in self.records:
            raise ValueError(f"duplicate object ID {object_id}: {self.records[object_id]['path']} and {relative}")
        kind = data["kind"]
        if kind == "PRIME_CANON_CANDIDATE_CLAIM_V1":
            match = CLAIM_PATH.fullmatch(relative)
            subject = match.group(1) if match else None
            sequence = match.group(2) if match else None
            expected_id = f"pc.candidate_claim.{subject}.{sequence}" if match else None
            legacy_example_id = f"pc.claim.example-subject.{sequence}" if subject == "example-subject" else None
            if (not match or object_id not in (expected_id, legacy_example_id) or data.get("spec", {}).get("claim_id") != object_id
                    or identity.get("subject_slug") != subject
                    or identity.get("corpus_id") != f"pc.corpus.{subject}"
                    or data.get("spec", {}).get("corpus_id") != f"pc.corpus.{subject}"):
                raise ValueError(f"candidate claim identity, subject, corpus or path mismatch: {relative}")
        self.records[object_id] = {
            "kind": str(kind),
            "subject": str(identity.get("subject_slug", "")),
            "corpus": str(identity.get("corpus_id", "")),
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }


def index_repo(repo: Path) -> ObjectIndex:
    repo = repo.resolve(strict=True)
    index = ObjectIndex()
    count = 0
    total = 0
    for root in ROOTS:
        directory = repo / root
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.yaml")):
            count += 1
            if count > MAX_OBJECTS:
                raise ValueError("object count exceeds identity-index budget")
            relative = path.relative_to(repo).as_posix()
            raw = read_bytes(repo, relative, limit=MAX_OBJECT_BYTES)
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("identity-index input exceeds aggregate budget")
            data = yaml.safe_load(raw)
            if isinstance(data, dict):
                index.add(relative, data, raw)
    return index
