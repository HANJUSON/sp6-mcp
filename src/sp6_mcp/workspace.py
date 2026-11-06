"""Path policy: where inputs are read from and where outputs may be written.

* Inputs (templates, backups) resolve against the work root.
* Outputs may only be created inside the artifacts directory (README 6.9).

Configure with ``SP6_MCP_WORK_ROOT`` and ``SP6_MCP_ARTIFACTS_DIR``; defaults are
the current directory and ``<work root>/artifacts``.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath

from .errors import OUTPUT_PATH_DENIED, SOURCE_NOT_FOUND, Sp6Error


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


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
        for part in pure.parts:
            stem = part.split(".")[0].upper()
            if ":" in part or part != part.rstrip(". ") or stem in _RESERVED or any(c in part for c in '<>"|?*'):
                raise Sp6Error(OUTPUT_PATH_DENIED, f"output_path part {part!r} is not a safe Windows file name", "output_path")
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
    """Create ``path`` exclusively. Never overwrites, never deletes a file it did not create."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".sp6tmp-", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)  # fails if path exists: no overwrite
        except FileExistsError:
            raise Sp6Error(OUTPUT_PATH_DENIED, "output_path already exists; choose a new name", "output_path") from None
        except OSError:
            # File systems without hard links: exclusive create instead.
            try:
                with open(path, "xb") as fh:
                    fh.write(data)
            except FileExistsError:
                raise Sp6Error(OUTPUT_PATH_DENIED, "output_path already exists; choose a new name", "output_path") from None
    finally:
        tmp.unlink(missing_ok=True)
