import os
from pathlib import Path

from core.models import FileChange, TestFile


def write_changes(
    root: Path, file_changes: list[FileChange], test_files: list[TestFile]
) -> None:
    for change in file_changes:
        target = root / change.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.full_content, encoding="utf-8")
    for test_file in test_files:
        target = root / test_file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(test_file.content, encoding="utf-8")


def normalize_permissions(root: Path) -> None:
    # Windows bind mounts don't carry real POSIX permission bits, which can make
    # every file look executable to the container -- normalize so linters don't
    # flag spurious "executable with no shebang" issues.
    for path in root.rglob("*"):
        if path.is_file():
            os.chmod(path, 0o644)
