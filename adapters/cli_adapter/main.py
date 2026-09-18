"""`agentdev` console-script entry point. Dispatches to subcommands; each
subcommand's actual logic lives in its own module so this stays a thin router.
"""

import argparse
import sys

from adapters.cli_adapter.version import __git_sha__, __version__


def _cmd_init(args: argparse.Namespace) -> int:
    from adapters.cli_adapter import init as init_cmd

    return init_cmd.main(args)


def _cmd_doctor(args: argparse.Namespace) -> int:
    from adapters.cli_adapter import doctor as doctor_cmd

    return doctor_cmd.main(args)


def _cmd_run(args: argparse.Namespace) -> int:
    from adapters.cli_adapter import run as run_cmd

    return run_cmd.main(args.extra)


def _cmd_graph_run(args: argparse.Namespace) -> int:
    from adapters.langgraph_adapter import run as graph_run_cmd

    return graph_run_cmd.main(args.extra)


def _cmd_check(args: argparse.Namespace) -> int:
    from adapters.cli_adapter import check_pr as check_cmd

    return check_cmd.main(args.extra)


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    from adapters.cli_adapter import bootstrap as bootstrap_cmd

    return bootstrap_cmd.main(args.extra)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentdev", description="Automated coding-agent CLI.")
    parser.add_argument(
        "--version", action="version", version=f"agentdev {__version__} ({__git_sha__})"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="Interactive per-project setup")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing .agentdev.toml")
    p_init.set_defaults(func=_cmd_init)

    p_doctor = subparsers.add_parser("doctor", help="Preflight checks for the current project")
    p_doctor.set_defaults(func=_cmd_doctor)

    # add_help=False on run/check: their real flags live in run.py/check_pr.py's
    # own parsers (shared with `python -m ...` via add_arguments), so -h/--help
    # here falls through to `extra` and is handled by that parser instead --
    # this keeps --help/--version at the top level from importing run.py's
    # (heavy: anthropic/docker/git/github) dependency chain just to build a parser.
    p_run = subparsers.add_parser("run", help="Run the requirement -> code -> PR loop", add_help=False)
    p_run.set_defaults(func=_cmd_run)

    p_graph_run = subparsers.add_parser(
        "graph-run",
        help="Run the requirement -> plan -> code -> tests -> review loop via LangGraph, with a plan-approval gate",
        add_help=False,
    )
    p_graph_run.set_defaults(func=_cmd_graph_run)

    p_check = subparsers.add_parser("check", help="Check a PR's human review status", add_help=False)
    p_check.set_defaults(func=_cmd_check)

    p_bootstrap = subparsers.add_parser(
        "bootstrap", help="First-time Project Intelligence indexing for this repo", add_help=False
    )
    p_bootstrap.set_defaults(func=_cmd_bootstrap)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    args.extra = extra
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
