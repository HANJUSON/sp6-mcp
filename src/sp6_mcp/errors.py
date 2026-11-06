"""Structured errors shared by the core, CLI and MCP layers."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

# Error codes listed in README section 7.1.
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
UNSUPPORTED_FIELD = "UNSUPPORTED_FIELD"
INVALID_SPEC = "INVALID_SPEC"
SOURCE_CHANGED = "SOURCE_CHANGED"
SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
DEPENDENCY_MISMATCH = "DEPENDENCY_MISMATCH"
SLOT_OCCUPIED = "SLOT_OCCUPIED"
SLOT_STATE_UNKNOWN = "SLOT_STATE_UNKNOWN"
OUTPUT_PATH_DENIED = "OUTPUT_PATH_DENIED"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass
class Sp6Error(Exception):
    """An error with a stable machine-readable code.

    ``device_write_state`` is always ``"none"`` in this offline build: no code
    path in this package opens a MIDI port or sends anything to an instrument.
    """

    code: str
    message: str
    field: str | None = None
    retryable: bool = False
    details: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        where = f" [{self.field}]" if self.field else ""
        return f"{self.code}{where}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "retryable": self.retryable,
            "device_write_state": "none",
            **({"details": self.details} if self.details else {}),
        }
