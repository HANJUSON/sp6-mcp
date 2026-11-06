"""Path policy: where inputs are read from and where outputs may be written.

* Inputs (templates, backups) resolve against the work root.
* Outputs may only be created inside the artifacts directory (README 6.9).

Configure with ``SP6_MCP_WORK_ROOT`` and ``SP6_MCP_ARTIFACTS_DIR``; defaults are
the current directory and ``<work root>/artifacts``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePath

from .errors import OUTPUT_PATH_DENIED, SOURCE_NOT_FOUND, Sp6Error


@dataclass(frozen=True)
class Workspace:
    work_root: Path
    artifacts_dir: Path

    @classmethod
    def from_env(cls) -> "Workspace":
        root = Path(os.environ.get("SP6_MCP_WORK_ROOT") or Path.cwd()).resolve()
        artifacts = os.environ.get("SP6_MCP_ARTIFACTS_DIR")
        return cls(root, Path(artifacts).resolve() if artifacts else root / "artifacts")

    def input_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.work_root / path
        path = path.resolve()
        if not path.is_file():
            raise Sp6Error(SOURCE_NOT_FOUND, f"Input file not found: {value}", "source_path")
        return path

    def output_path(self, value: str, *, protect: tuple[Path, ...] = ()) -> Path:
        """Validate a new output path relative to the artifacts directory."""
        pure = PurePath(value)
        if not value or pure.is_absolute() or pure.drive or pure.root:
            raise Sp6Error(OUTPUT_PATH_DENIED, "output_path must be relative to the artifacts directory", "output_path")
        if any(part == ".." for part in pure.parts):
            raise Sp6Error(OUTPUT_PATH_DENIED, "output_path may not contain '..'", "output_path")
        base = self.artifacts_dir.resolve()
        base.mkdir(parents=True, exist_ok=True)
        target = (base / pure).resolve()  # resolves symlinks / junctions that exist
        try:
            target.relative_to(base)
        except ValueError:
            raise Sp6Error(OUTPUT_PATH_DENIED, "output_path resolves outside the artifacts directory", "output_path") from None
        if target.exists():
            raise Sp6Error(OUTPUT_PATH_DENIED, "output_path already exists; choose a new name", "output_path")
        for protected in protect:
            if target == protected.resolve():
                raise Sp6Error(OUTPUT_PATH_DENIED, "output_path equals an input file", "output_path")
        return target


def write_new_file(path: Path, data: bytes) -> None:
    """Create ``path`` exclusively; remove partial output on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    try:
        with open(partial, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        # os.link + unlink keeps "never overwrite" semantics on Windows too.
        os.link(partial, path)
    finally:
        if partial.exists():
            partial.unlink()
