from __future__ import annotations

import json

from app.storage.access_log import GENESIS_HASH, HashChainedAccessLog, verify_chain
from app.storage.client import StorageLocation

LOCATION = StorageLocation(institution_slug="sbl-gh-001", tier="canonical", object_path="p/x")


def recorded_log(operations: int = 5) -> HashChainedAccessLog:
    log = HashChainedAccessLog(identity="test-suite")
    for index in range(operations):
        log("write" if index % 2 == 0 else "read", LOCATION, version_id=f"v{index}")
    return log


class TestChainConstruction:
    def test_first_entry_chains_from_genesis(self) -> None:
        log = recorded_log(1)
        assert log.entries[0].prev_hash == GENESIS_HASH
        assert log.entries[0].entry_hash

    def test_each_entry_chains_from_the_previous(self) -> None:
        log = recorded_log(4)
        for previous, current in zip(log.entries, log.entries[1:], strict=False):
            assert current.prev_hash == previous.entry_hash

    def test_entries_capture_operation_details(self) -> None:
        log = recorded_log(1)
        entry = log.entries[0]
        assert entry.operation == "write"
        assert entry.institution_slug == "sbl-gh-001"
        assert entry.tier == "canonical"
        assert entry.identity == "test-suite"
        assert entry.result == "success"


class TestVerification:
    def test_intact_chain_verifies(self) -> None:
        intact, detail = verify_chain(recorded_log(10).export_jsonl())
        assert intact, detail

    def test_empty_chain_verifies(self) -> None:
        intact, _ = verify_chain("")
        assert intact

    def test_tampered_field_is_detected_at_the_right_entry(self) -> None:
        lines = recorded_log(6).export_jsonl().splitlines()
        tampered = json.loads(lines[3])
        tampered["object_path"] = "somewhere/else"  # rewrite history
        lines[3] = json.dumps(tampered, sort_keys=True)
        intact, detail = verify_chain("\n".join(lines))
        assert not intact
        assert "sequence 3" in detail

    def test_removed_entry_breaks_the_chain(self) -> None:
        lines = recorded_log(6).export_jsonl().splitlines()
        del lines[2]
        intact, detail = verify_chain("\n".join(lines))
        assert not intact
