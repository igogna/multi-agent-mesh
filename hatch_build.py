"""Hatchling build hook: bake the git SHA at build time into a generated
module, since an installed wheel has no .git directory to read from at
runtime (see adapters/cli_adapter/_build_info.py, generated below)."""

import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class GitSHABuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        sha = "unknown"
        try:
            sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.root,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            pass

        target = Path(self.root) / "adapters" / "cli_adapter" / "_build_info.py"
        target.write_text(
            f"__version__ = {self.metadata.version!r}\n__git_sha__ = {sha!r}\n",
            encoding="utf-8",
        )
