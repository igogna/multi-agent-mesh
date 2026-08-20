import os
import re
from fnmatch import fnmatch
from pathlib import Path

IGNORE_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache"}
MAX_FILES = 200
MAX_FILE_BYTES = 50_000
MAX_GREP_MATCHES = 100


def _walk_relative_paths(repo_path: str) -> list[str]:
    root = Path(repo_path)
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for filename in filenames:
            full_path = Path(dirpath) / filename
            paths.append(full_path.relative_to(root).as_posix())
    return sorted(paths)


def list_files(repo_path: str, pattern: str | None = None) -> list[str]:
    paths = _walk_relative_paths(repo_path)
    if pattern:
        paths = [p for p in paths if fnmatch(p, pattern)]
    return paths[:MAX_FILES]


def read_file(repo_path: str, file_path: str) -> str:
    root = Path(repo_path).resolve()
    target = (root / file_path).resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"path {file_path!r} escapes repo_path")
    if not target.is_file():
        raise FileNotFoundError(f"{file_path} not found under {repo_path}")

    data = target.read_bytes()
    truncated = len(data) > MAX_FILE_BYTES
    text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += "\n... [truncated]"
    return text


def grep(repo_path: str, pattern: str) -> list[str]:
    root = Path(repo_path)
    regex = re.compile(pattern)
    matches = []
    for relpath in _walk_relative_paths(repo_path):
        full_path = root / relpath
        if full_path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            if regex.search(line):
                matches.append(f"{relpath}:{lineno}:{line}")
                if len(matches) >= MAX_GREP_MATCHES:
                    return matches
    return matches
