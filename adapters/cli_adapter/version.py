"""Resolves the installed version + the git SHA it was built from. Shared by
main.py (--version) and run.py (the run log, see tools/run_log.py) so both
report the same value without duplicating the resolution logic.

_build_info.py is generated at wheel-build time (see hatch_build.py) --
an installed wheel has no .git directory to read from at runtime, so the SHA
has to be baked in ahead of time.
"""

import subprocess
from pathlib import Path

try:
    from adapters.cli_adapter._build_info import __git_sha__, __version__
except ImportError:
    # No _build_info.py -- running from a source checkout / editable install
    # rather than a built wheel.
    __version__ = "0.0.0+dev"
    try:
        __git_sha__ = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parent,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
        )
    except (OSError, subprocess.CalledProcessError):
        __git_sha__ = "unknown"


def version_string() -> str:
    return f"{__version__} ({__git_sha__})"
