"""Cause classification: map a failing step's captured output to one of the
mechanically-identifiable cause classes, or 'unclassified' when no signature
matches. Guessing a cause is exactly the overclaim this tool exists to catch,
so every pattern here corresponds to a distinctive, well-known error
signature -- never a heuristic guess."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Iterable, Optional

_UNDECLARED_DEP = re.compile(
    r"ModuleNotFoundError: No module named '([\w\.]+)'|Cannot find module '([^']+)'"
)
# An IndexError raised at/near a `.parents[N]` access -- the signature of a
# path-depth assumption baked into a conftest/module at import time.
_PATH_DEPTH = re.compile(
    r"IndexError[\s\S]{0,400}?parents\[\d+\]|parents\[\d+\][\s\S]{0,400}?IndexError"
)
_MISSING_SERVICE = re.compile(
    r"ConnectionRefusedError|Connection refused|ECONNREFUSED|"
    r"could not connect to server|getaddrinfo failed"
)
# A GENUINE platform-mismatch signature only. `npm ci` fails for many
# reasons that have nothing to do with platform -- a typo'd package name
# (E404), an out-of-sync lockfile (EUSAGE), a missing lockfile (EUSAGE) --
# and none of those carry a platform dimension. Mapping every npm ci
# failure onto `lockfile-platform-mismatch` is exactly the "guessing a
# cause" overclaim this tool exists to catch, so this pattern is
# deliberately narrow: it only fires on npm's own EBADPLATFORM code, its
# "Unsupported platform" text, or an explicit os/cpu/libc
# wanted-vs-current mismatch report.
_PLATFORM_MISMATCH = re.compile(
    r"npm (?:ERR!|error) code EBADPLATFORM|"
    r"Unsupported platform|"
    r"notsup Valid (?:os|cpu|libc)|"
    r"wanted\s*\{[^}]*(?:\"os\"|\"cpu\"|\"libc\")[^}]*\}\s*\(current:"
)
_FILE_NOT_FOUND = re.compile(
    r"(?:FileNotFoundError|No such file or directory)[^\n]*?['\"]([^'\"]+)['\"]"
)
_PERMISSION = re.compile(r"Permission")


def load_gitignore_patterns(repo_path: Path) -> list[str]:
    """Read the SOURCE repo's own .gitignore (not the clone's) so an
    untracked-file failure inside the container can be cross-referenced
    against what the repo itself declares as ignored."""
    gi = Path(repo_path) / ".gitignore"
    if not gi.exists():
        return []
    patterns = []
    for line in gi.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.rstrip("/"))
    return patterns


def _is_gitignored(path_str: str, patterns: Iterable[str]) -> bool:
    name = Path(path_str).name
    norm = path_str.lstrip("/")
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(norm, pat) or norm.endswith("/" + pat):
            return True
    return False


def classify(
    step_name: str,
    output: str,
    *,
    run_as_root: bool = False,
    gitignore_patterns: Optional[list[str]] = None,
) -> str:
    if _UNDECLARED_DEP.search(output):
        return "undeclared-dependency"

    if _PATH_DEPTH.search(output):
        return "path-depth-assumption"

    m = _FILE_NOT_FOUND.search(output)
    if m and gitignore_patterns and _is_gitignored(m.group(1), gitignore_patterns):
        return "untracked-file-dependency"

    if _MISSING_SERVICE.search(output):
        return "missing-service"

    if _PLATFORM_MISMATCH.search(output):
        return "lockfile-platform-mismatch"

    # Only classify as a root-vs-non-root discrepancy when we know we ran as
    # root AND the failure text itself is about a permission assertion --
    # never guess this from a non-root run.
    if run_as_root and _PERMISSION.search(output):
        return "root-only-permission-behaviour"

    return "unclassified"
