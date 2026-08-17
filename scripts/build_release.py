#!/usr/bin/env python3
"""Create a clean RecallZero source archive and SHA-256 checksum."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "recallzero_v2"

SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
}
SKIP_FILE_NAMES = {".env", ".DS_Store"}


def should_include(path: Path) -> bool:
    relative = path.relative_to(PROJECT_ROOT)
    if any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in relative.parts[:-1]):
        return False
    if path.name in SKIP_FILE_NAMES or path.suffix in {".pyc", ".pyo"}:
        return False
    if path.suffix == ".zip" or path.name.endswith(".zip.sha256"):
        return False
    if relative.parts and relative.parts[0] == "data":
        # Ship empty evidence directories and documentation, never generated evidence.
        if relative.parts[:2] in {
            ("data", "raw"),
            ("data", "normalized"),
            ("data", "cache"),
            ("data", "runs"),
        }:
            return path.name == ".gitkeep"
    return True


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_archive(output: Path) -> tuple[Path, Path, int]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    files = sorted(path for path in PROJECT_ROOT.rglob("*") if path.is_file() and should_include(path))
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(PROJECT_ROOT)
            archive.write(path, Path(ARCHIVE_ROOT) / relative)

    checksum_path = output.with_suffix(output.suffix + ".sha256")
    checksum_path.write_text(f"{sha256(output)}  {output.name}\n", encoding="ascii")
    return output, checksum_path, len(files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT.parent / "RecallZero_v2_GB10_AIQ.zip",
    )
    args = parser.parse_args()
    archive, checksum, file_count = build_archive(args.output)
    print(f"Created {archive} ({file_count} files)")
    print(f"Created {checksum}")


if __name__ == "__main__":
    main()
