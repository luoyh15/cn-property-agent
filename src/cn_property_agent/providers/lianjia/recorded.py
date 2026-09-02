"""Shared plumbing for the recorded (local JSON) Lianjia providers.

Recorded providers replay rows captured earlier and written to a file. They
perform no network, browser or clock access: batch provenance is part of the
snapshot document, and field interpretation is delegated in full to the Lianjia
parsers.

Everything here concerns the *document*, not its rows: reading the file,
decoding JSON, and the one error type raised when a snapshot cannot be read as a
batch at all. Row-level failures are the parsers' business and stay visible as
:class:`~cn_property_agent.providers.ParseRejection` values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError


class LianjiaSnapshotError(Exception):
    """A recorded snapshot could not be read as a Lianjia batch.

    Covers a missing/unreadable file, invalid JSON, a wrong top-level shape,
    invalid batch metadata, a non-array ``rows``, and a batch recorded for a
    different community than the one requested. Raised rather than returning an
    empty result so a broken input can never be mistaken for "nothing was
    recorded".
    """

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"lianjia snapshot {path}: {message}")
        self.path = path


def load_snapshot_document(path: Path) -> dict[str, Any]:
    """Read one snapshot file as a JSON object, or raise :class:`LianjiaSnapshotError`.

    Validating the batch fields is left to the caller's snapshot model; this
    only guarantees that there is a mapping to validate.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise LianjiaSnapshotError(path, f"cannot be read: {error}") from error
    except UnicodeDecodeError as error:
        raise LianjiaSnapshotError(path, f"is not valid UTF-8: {error}") from error

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise LianjiaSnapshotError(path, f"is not valid JSON: {error}") from error

    if not isinstance(payload, dict):
        raise LianjiaSnapshotError(
            path,
            f"expected a JSON object at the top level, got {type(payload).__name__}",
        )
    return payload


def format_validation_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
