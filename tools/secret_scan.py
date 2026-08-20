import re

_PLACEHOLDER_MARKERS = (
    "xxx", "changeme", "your_", "example", "dummy", "<<", "redacted", "placeholder", "todo",
)

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
]

_GENERIC_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"']([A-Za-z0-9_\-/+=]{16,})[\"']"
)

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _findings_in_line(added_line: str) -> list[str]:
    findings = []
    for label, pattern in _PATTERNS:
        for match in pattern.finditer(added_line):
            findings.append(f"{label}: {_mask(match.group(0))}")
    for match in _GENERIC_ASSIGNMENT.finditer(added_line):
        value = match.group(2)
        if not _looks_like_placeholder(value):
            findings.append(f"Possible {match.group(1)}: {_mask(value)}")
    return findings


def scan(diff: str) -> list[str]:
    """Scan a unified diff for likely secrets in newly added lines only."""
    issues: list[str] = []
    current_file = "?"
    new_line_no = 0

    for line in diff.splitlines():
        if line.startswith("+++ "):
            current_file = line[4:].removeprefix("b/")
            continue
        if line.startswith("---"):
            continue

        hunk_match = _HUNK_HEADER.match(line)
        if hunk_match:
            new_line_no = int(hunk_match.group(1))
            continue

        if line.startswith("+"):
            for finding in _findings_in_line(line[1:]):
                issues.append(f"{current_file}:{new_line_no}: {finding}")
            new_line_no += 1
        elif line.startswith("-"):
            continue
        else:
            new_line_no += 1

    return issues
