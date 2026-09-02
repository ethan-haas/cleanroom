"""Fixtures live under fixtures/ as PLAIN tracked files in the cleanroom
repo itself, so a fresh clone of the repo is self-contained. Committing
them as nested git repos with no .gitmodules meant a fresh clone yielded
empty fixture directories -- hence plain files.

cleanroom needs a real git repository to `git archive HEAD` from, so the
gate suite materializes each fixture into a throwaway git repo under a
tempdir at RUN TIME, right before pointing the tool at it. This is the
single choke point every gate test goes through -- including the
dogfood test that fresh-clones the cleanroom repo itself.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

_GIT_ENV = ["-c", "user.email=cleanroom-tests@local", "-c", "user.name=cleanroom-tests"]


def materialize_git_repo(source_dir: Path, dest_dir: Path) -> Path:
    """Copy source_dir's plain files into dest_dir and turn dest_dir into a
    single-commit git repo. Returns dest_dir.

    A `<name>.seed` file becomes `<name>` in the materialized worktree,
    written AFTER the commit so it is present on disk but absent from HEAD.

    That indirection is the whole point. The untracked-file fixture needs a
    file that exists in the worktree and is NOT in the repository -- which is
    exactly a file that cannot itself be committed to this repository either.
    This used to just copy whatever was lying in source_dir and rely on
    `secret.local` being physically present, which held only on the machine
    that first created it: from a fresh clone the file did not exist, so the
    fixture's worktree half asserted a pass it could never get. Seeding it
    here makes the fixture reproduce from a clean checkout."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, dest_dir, dirs_exist_ok=True)

    # Lift the seeds out before the commit so they reach neither HEAD nor the
    # worktree under their seed name, then restore them as untracked files.
    seeded: list[tuple[Path, bytes]] = []
    for seed in sorted(dest_dir.rglob("*.seed")):
        seeded.append((seed.with_suffix(""), seed.read_bytes()))
        seed.unlink()

    subprocess.run(["git", "init", "-q"], cwd=dest_dir, check=True)
    subprocess.run(["git", *_GIT_ENV, "add", "-A"], cwd=dest_dir, check=True)
    subprocess.run(
        ["git", *_GIT_ENV, "commit", "-q", "-m", f"materialized: {source_dir.name}"],
        cwd=dest_dir,
        check=True,
    )

    for target, content in seeded:
        target.write_bytes(content)
    return dest_dir


def init_git_repo(existing_dir: Path) -> Path:
    """Turn an already-populated directory into a single-commit git repo in
    place (no copy) -- for ad hoc repros built directly under a tmp dir."""
    subprocess.run(["git", "init", "-q"], cwd=existing_dir, check=True)
    subprocess.run(["git", *_GIT_ENV, "add", "-A"], cwd=existing_dir, check=True)
    subprocess.run(
        ["git", *_GIT_ENV, "commit", "-q", "-m", "ad hoc repro"],
        cwd=existing_dir,
        check=True,
    )
    return existing_dir


def materialize_fixture(fixtures_root: Path, name: str, tmp_base: Path) -> Path:
    dest = tmp_base / name
    return materialize_git_repo(fixtures_root / name, dest)


def new_tmp_base(prefix: str = "cleanroom-gate-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))
