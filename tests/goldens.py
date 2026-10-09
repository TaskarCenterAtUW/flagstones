"""Hashing that binds each golden fixture to the algorithm version it
came from."""

import hashlib
import json


def digest(golden: dict) -> str:
    """Hash the fixture after dropping its `algorithm_version` key.

    Stripping that key pins the fixture's content only, so restamping
    a version cannot move this digest. That way `test_golden` scores version
    change against output change honestly."""
    body = {k: v for k, v in golden.items() if k != "algorithm_version"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
