def list_files(repo_path: str, pattern: str | None = None) -> list[str]:
    return ["stub_file.py"]


def read_file(repo_path: str, file_path: str) -> str:
    return "# stub file content\n"


def grep(repo_path: str, pattern: str) -> list[str]:
    return []
