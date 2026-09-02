# cleanroom

Your tests pass on your machine. This proves whether they pass on a **fresh clone of your
repository, in a container matching your CI**, before you push -- and names the specific reason
when they do not.

## Why this exists

Running a suite from a clean clone in a CI-matching container surfaces defects a developer
machine structurally cannot show. One per cause class this tool reports:

| defect | why the dev machine hid it |
|---|---|
| `conftest` resolved a path as `parents[2]` at import time | the checkout was deep enough; a clone into a shallow path raised `IndexError` and the entire suite failed to collect |
| a package was imported at module level, undeclared in the manifest | it was already installed globally on the dev box |
| a lockfile omitted a Linux-only optional subtree | generated on a different platform; `npm ci` fails on the runner |
| a test read a gitignored internal file | the untracked file was sitting on disk |
| code connected to a service with no fallback and none declared | the developer's local service was always running |
| a test only passed because it ran as root | root bypasses the permission bits the test was asserting |

Each of those ships a repository that looks green. `cleanroom` answers exactly one
question: does this repo actually work from a clean checkout, in the container your CI uses,
installing only what you declared, as a non-root user? And it never reports a check that did not
run as a check that passed.

## Install

```
pip install -e .
```

Requires a container runtime (`docker`) on PATH. Its absence is reported honestly, never
silently treated as a pass -- see gate 6 below.

## Declaring commands that work as a non-root user

Declared commands run as a **non-root user by default**, which is the whole point -- running them
as root is how a permission-dependent test passes in the cleanroom and fails in CI. One
consequence is worth stating plainly, because it will be the first thing most Python projects
hit: `pip install -e .` cannot write to the image's system `site-packages` as a non-root user,
so it fails. Install somewhere the user owns instead:

```toml
[cleanroom]
install = "pip install --target=.cleanroom-deps -r requirements.txt"
test = "PYTHONPATH=.cleanroom-deps python -m pytest -q"
```

`--root` exists for the cases where you genuinely mean it, and is opt-in only.

Pick the image your CI actually uses, not the smallest one that looks right. A `-slim` image
omits system tools your suite may quietly rely on -- running `cleanroom` against its own
repository fails on `python:3.12-slim` and passes on `python:3.12`, because the fixture harness
shells out to `git` and the slim image does not ship it.

## Usage

```
cleanroom                      # infer everything, run against ./
cleanroom --repo path/to/repo
cleanroom --root               # run declared commands as root (opt-in only)
cleanroom --clone-root /some/path
cleanroom --json
```

Exit code is non-zero on any failure, so it doubles as a pre-push hook or a CI job of its own.

## What it does

1. Clones the repository **at HEAD, tracked files only** (`git archive HEAD`, never a copy of the
   working tree) into a deliberately **shallow path inside the container** (`/r` by default), so a
   `Path(__file__).resolve().parents[N]`-style assumption breaks here, not in CI.
2. Runs it inside a container image matching the project's declared CI (language version
   included), inferred from `.github/workflows/*` or the manifest, overridable in
   `.cleanroom.toml`.
3. Installs using only the repo's own declared manifest (`pip install` from `requirements.txt`/
   `pyproject.toml`; `npm ci` when `package-lock.json` is present, `npm install` otherwise). No
   implicit extra installs.
4. Runs the declared install/test commands as a **non-root user by default**; root only when you
   pass `--root` explicitly.
5. Reports each step as exactly one of `pass` / `fail` / `not-run-because-<reason>`, and on
   failure names the cause class where it is mechanically identifiable.

## Cause classes

`undeclared-dependency` · `path-depth-assumption` · `untracked-file-dependency` ·
`missing-service` · `lockfile-platform-mismatch` · `root-only-permission-behaviour` ·
`unclassified`

An unclassified failure is reported as `unclassified`. Guessing a cause is the exact overclaim
this tool exists to catch.

## Configuration -- `.cleanroom.toml`

Every key is optional; see `.cleanroom.toml.sample`. Absent config falls back to sane inference
from the repo's own manifest and declared CI.

Keys go under a `[cleanroom]` table or at the top level -- nothing else is read. This file is not
`pyproject.toml`, so `[tool.cleanroom]` and `[project]` are **not** recognized, and a key the tool
cannot place is a hard error naming it rather than a setting that silently does nothing.

```toml
[cleanroom]
image = "python:3.12-slim"
install = "pip install --target=.cleanroom-deps -r requirements.txt"
test = "python -m pytest -q"
clone_root = "/r"
non_root_uid = "1000:1000"
```

## Fixtures

`fixtures/` holds one tiny, real, committed git repository per defect class (plus two genuinely
clean repositories), each reproducing exactly one failure mode. `tests/test_gates.py` runs the
real tool against every one of them and asserts the reported cause class, not merely a non-zero
exit code.

## Scope

Language-agnostic core; the runner shells declared commands and does not know what `pytest` is.
Python and Node detectors are shipped because they are testable here; the interface is open for
more. Not a CI replacement, not a test framework, not a linter -- it answers exactly one question.

## Guardrails

- No network in the tool's own logic beyond pulling the declared image.
- Never mutates the working tree, never pushes anything.
- A container runtime is a dependency; its absence is reported as `not-run-because-no-container-runtime`
  on every step, and the process exits non-zero -- never `pass`.
