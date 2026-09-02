from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .runner import not_run_report, run_cleanroom


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cleanroom",
        description=(
            "Prove your tests pass on a fresh clone of your repository, in a "
            "container matching your CI, before you push."
        ),
    )
    parser.add_argument("--repo", default=".", help="path to the git repository (default: cwd)")
    parser.add_argument("--root", action="store_true",
                         help="run declared commands as root (explicit opt-in only)")
    parser.add_argument("--clone-root", default=None,
                         help="override the in-container clone path")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    repo_path = Path(args.repo).resolve()
    overrides = {}
    if args.clone_root:
        overrides["clone_root"] = args.clone_root

    try:
        cfg = load_config(repo_path, overrides=overrides)
    except ConfigError as e:
        report = not_run_report("malformed-config")
        if args.json:
            print(json.dumps({
                "steps": [
                    {"name": s.name, "status": s.status, "cause_class": s.cause_class}
                    for s in report.steps
                ],
                "exit_code": report.exit_code,
                "error": str(e),
            }, indent=2))
        else:
            print(f"config: {e}", file=sys.stderr)
            for s in report.steps:
                print(f"{s.name}: {s.status}")
        return report.exit_code

    report = run_cleanroom(repo_path, cfg, run_as_root=args.root)

    if args.json:
        print(json.dumps({
            "steps": [
                {"name": s.name, "status": s.status, "cause_class": s.cause_class}
                for s in report.steps
            ],
            "exit_code": report.exit_code,
        }, indent=2))
    else:
        for s in report.steps:
            line = f"{s.name}: {s.status}"
            if s.cause_class:
                line += f" ({s.cause_class})"
            print(line)

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
