"""Small explicit target service for run-core tests unrelated to profile construction."""
import hashlib
import json
from pathlib import Path


class FixtureTargetService:
    def build(self, kind, ref, destination):
        root = Path(destination)
        (root / "source").mkdir(parents=True)
        (root / "profile").mkdir(parents=True)
        return {
            "kind": kind,
            "ref": ref,
            "version": "fixture",
            "revision": "a" * 40,
            "draft": ref if kind == "draft" else None,
            "config_digest": hashlib.sha256(
                json.dumps({}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "source_path": str(root / "source"),
            "profile_path": str(root / "profile"),
        }
