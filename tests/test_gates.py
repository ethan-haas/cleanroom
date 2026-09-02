"""One assertion per acceptance gate, run against the real tool against the
real fixtures. These are self-verification only -- a test here is written to
assert the behaviour, never patched to make a run go green.

Fixtures live under fixtures/ as plain tracked files (they used to
be nested git repos/gitlinks, which meant a fresh clone of this repo yielded
empty fixture directories). Every test here goes through
helpers.materialize_fixture(), which turns the plain files into a throwaway
git repo at run time before pointing cleanroom at it -- exactly what a real
consumer of this repo would have to do too, and what the fresh-clone dogfood
test at the bottom proves works end to end.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cleanroom.config import load_config
from cleanroom.docker_runtime import docker_available
from cleanroom.runner import run_cleanroom

from helpers import init_git_repo, materialize_fixture, materialize_git_repo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]

requires_docker = pytest.mark.skipif(
    not docker_available(), reason="docker runtime not available"
)

DEFECT_FIXTURES = {
    "undeclared-dependency": "undeclared-dependency",
    "path-depth-assumption": "path-depth-assumption",
    "untracked-file-dependency": "untracked-file-dependency",
    "missing-service": "missing-service",
    "lockfile-platform-mismatch": "lockfile-platform-mismatch",
}


@pytest.fixture
def tmp_base(tmp_path_factory):
    return tmp_path_factory.mktemp("materialized")


def _materialized(name, tmp_base):
    return materialize_fixture(FIXTURES, name, tmp_base)


def _run(name, tmp_base, **kwargs):
    repo = _materialized(name, tmp_base)
    overrides = kwargs.pop("overrides", None)
    cfg = load_config(repo, overrides=overrides)
    return run_cleanroom(repo, cfg, **kwargs)


# --- Gate 1: every defect class is reproduced and caught, cause class matches -----

@requires_docker
@pytest.mark.parametrize("fixture_name,expected_cause", sorted(DEFECT_FIXTURES.items()))
def test_g1_defect_fixtures_detected_with_cause(fixture_name, expected_cause, tmp_base):
    report = _run(fixture_name, tmp_base)
    assert report.exit_code != 0
    failing = [s for s in report.steps if s.status == "fail"]
    assert failing, f"{fixture_name}: expected a failing step, got {report.steps}"
    assert any(s.cause_class == expected_cause for s in failing), (
        f"{fixture_name}: expected cause {expected_cause!r}, got "
        f"{[(s.name, s.cause_class) for s in failing]}"
    )


@requires_docker
def test_g1_root_only_fixture_detected_as_seventh_class(tmp_base):
    # The 7th defect class needs an explicit root run -- see gate 5.
    report = _run("root-only-permission-behaviour", tmp_base, run_as_root=True)
    assert report.exit_code != 0
    test_step = report.get("test")
    assert test_step.status == "fail"
    assert test_step.cause_class == "root-only-permission-behaviour"


# --- E1-E3 regression: a generic npm ci failure with no platform dimension
# must never be labeled lockfile-platform-mismatch. Reproduced through the
# FULL pipeline (not just the classify() unit tests), against a throwaway
# materialized repo, so the boundary is proven at the tool level too. -----

@requires_docker
def test_g1_typod_dependency_npm_failure_is_unclassified_not_platform(tmp_base):
    # E1: package.json references a package that does not exist at all.
    # npm ci fails E404 -- zero platform dimension.
    repo = tmp_base / "typo-dep"
    repo.mkdir()
    (repo / "package.json").write_text(
        '{"name":"typo-dep-repro","version":"1.0.0","private":true,'
        '"dependencies":{"left-pad-fixture-dep-does-not-exist-xyz":"^1.0.0"}}'
    )
    (repo / "package-lock.json").write_text(
        '{"name":"typo-dep-repro","version":"1.0.0","lockfileVersion":3,'
        '"requires":true,"packages":{"":{"name":"typo-dep-repro","version":"1.0.0"}}}'
    )
    (repo / ".cleanroom.toml").write_text(
        '[cleanroom]\nimage = "node:20-slim"\ninstall = "npm ci"\ntest = "true"\n'
    )
    init_git_repo(repo)
    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)

    assert report.exit_code != 0
    install_step = report.get("install")
    assert install_step.status == "fail"
    assert install_step.cause_class == "unclassified", (
        f"expected unclassified (no platform signature in a typo'd-package "
        f"failure), got {install_step.cause_class!r}"
    )


@requires_docker
def test_g1_missing_lockfile_npm_failure_is_unclassified_not_platform(tmp_base):
    # E3: package.json present, package-lock.json missing entirely -> EUSAGE.
    # No platform dimension either.
    repo = tmp_base / "no-lockfile"
    repo.mkdir()
    (repo / "package.json").write_text('{"name":"no-lockfile-repro","version":"1.0.0","private":true}')
    (repo / ".cleanroom.toml").write_text(
        '[cleanroom]\nimage = "node:20-slim"\ninstall = "npm ci"\ntest = "true"\n'
    )
    init_git_repo(repo)
    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)

    assert report.exit_code != 0
    install_step = report.get("install")
    assert install_step.status == "fail"
    assert install_step.cause_class == "unclassified"


# --- The core false-green shape. A repo with a
# genuinely FAILING test suite and `test = ""` must NEVER come back green --
# that is a false green, the exact failure mode this tool exists to prevent.
# config.py now rejects this at load time (see test_config.py), but this
# test proves the full pipeline end to end: the run must exit non-zero and
# must never report test: pass. --------------------------------------------

def test_a_smoking_gun_failing_test_with_blank_test_command_never_exits_zero(tmp_base, capsys):
    # No docker needed: config.py rejects `test = ""` before docker is even
    # checked, so this reproduces the failure through the real CLI (a repo
    # with a genuinely FAILING test suite and `test = ""` must never come
    # back green) without requiring a container runtime.
    from cleanroom.cli import main

    repo = tmp_base / "smoking-gun"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest\n")
    (repo / "t_test.py").write_text("def test_fail():\n    assert False\n")
    (repo / ".cleanroom.toml").write_text(
        '[cleanroom]\nimage = "python:3.12-slim"\n'
        'install = "pip install --target=.cleanroom-deps -r requirements.txt"\n'
        'test = ""\n'
    )
    init_git_repo(repo)

    exit_code = main(["--repo", str(repo), "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert exit_code != 0, "false green: exit 0 despite a genuinely failing test suite"
    assert payload["exit_code"] != 0
    test_status = next(s["status"] for s in payload["steps"] if s["name"] == "test")
    assert test_status != "pass", "false green: test: pass despite a genuinely failing test suite"
    assert test_status == "not-run-because-malformed-config"


@requires_docker
def test_a_runner_defensive_guard_blank_test_never_passes(tmp_base):
    # The belt-and-suspenders half: bypass load_config()'s own validation
    # entirely (hand-build a Config the way a future code path might) and
    # prove the RUNNER's own cfg.test.strip() guard independently stops the
    # false green, with docker genuinely available and the pipeline
    # actually running clone+install for real.
    from cleanroom.config import Config

    repo = _materialized("clean-python", tmp_base)
    cfg = load_config(repo)
    blank_test_cfg = Config(
        image=cfg.image, install=cfg.install, test="   ",
        clone_root=cfg.clone_root, non_root_uid=cfg.non_root_uid,
        language=cfg.language, source="config",
    )
    report = run_cleanroom(repo, blank_test_cfg)

    assert report.exit_code != 0
    test_step = report.get("test")
    assert test_step.status == "not-run-because-empty-command"
    assert test_step.status != "pass"


@requires_docker
def test_a_runner_defensive_guard_blank_install_never_silently_passes(tmp_base):
    from cleanroom.config import Config

    # clean-node: zero declared dependencies, test.js uses only node's
    # builtin `assert` -- so skipping install entirely doesn't ALSO starve
    # the test step of something it genuinely needs (unlike clean-python,
    # whose test step needs pytest itself, which the install step is what
    # provides -- that would conflate "install skipped" with "pytest
    # missing" and prove the wrong thing).
    repo = _materialized("clean-node", tmp_base)
    cfg = load_config(repo)
    blank_install_cfg = Config(
        image=cfg.image, install="", test=cfg.test,
        clone_root=cfg.clone_root, non_root_uid=cfg.non_root_uid,
        language=cfg.language, source="config",
    )
    report = run_cleanroom(repo, blank_install_cfg)

    install_step = report.get("install")
    assert install_step.status == "not-run-because-empty-command"
    assert install_step.status != "pass"
    # unlike a blank test, a blank install doesn't gate the whole run --
    # there may genuinely be nothing to install -- so test still executes
    # for real and can pass on its own merits.
    assert report.get("test").status == "pass"


# --- An oversized declared command must
# never crash uncaught (OSError E2BIG from subprocess) -- especially not in
# --json mode, where a raw traceback on stdout breaks the JSON contract for
# machine consumers entirely. ------------------------------------------------

@requires_docker
def test_b_oversized_command_yields_valid_report_not_a_crash(tmp_base):
    repo = tmp_base / "oversized-command"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest\n")
    (repo / "t_test.py").write_text("def test_ok():\n    assert True\n")
    oversized = "x" * 200_000
    (repo / ".cleanroom.toml").write_text(
        '[cleanroom]\nimage = "python:3.12-slim"\n'
        'install = "pip install --target=.cleanroom-deps -r requirements.txt"\n'
        f'test = "echo {oversized}"\n'
    )
    init_git_repo(repo)

    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)  # must not raise

    assert report.exit_code != 0
    test_step = report.get("test")
    assert test_step.status != "pass"
    assert test_step.status == "fail"


@requires_docker
def test_b_oversized_command_cli_json_mode_never_tracebacks(tmp_base, capsys):
    from cleanroom.cli import main

    repo = tmp_base / "oversized-command-cli"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest\n")
    (repo / "t_test.py").write_text("def test_ok():\n    assert True\n")
    oversized = "x" * 200_000
    (repo / ".cleanroom.toml").write_text(
        '[cleanroom]\nimage = "python:3.12-slim"\n'
        'install = "pip install --target=.cleanroom-deps -r requirements.txt"\n'
        f'test = "echo {oversized}"\n'
    )
    init_git_repo(repo)

    exit_code = main(["--repo", str(repo), "--json"])
    out = capsys.readouterr()

    assert exit_code != 0
    assert "Traceback" not in (out.out + out.err)
    payload = json.loads(out.out)  # must parse -- this is the whole point
    assert payload["exit_code"] != 0
    # clone/install legitimately pass (only the oversized test command
    # crashed the underlying subprocess call) -- the point is specifically
    # that the OVERSIZED-COMMAND step never comes back pass.
    test_status = next(s["status"] for s in payload["steps"] if s["name"] == "test")
    assert test_status != "pass"


# --- A deeper false green. A raw-text-only guard checks
# the RAW config string. A command that is non-blank as TEXT can still
# reduce to NOTHING once the container's shell actually parses/expands it
# -- a comment-only line, an unset variable, a whitespace-only expansion --
# and that ran as a silent shell no-op, exiting 0, reported test: pass,
# with a genuinely FAILING committed test present. This is the cardinal
# false green the tool exists to prevent, one layer deeper than a static
# string check can see: catching it requires actually asking the shell
# what it did (see _wrap_for_effective_empty_detection in runner.py). ------

def _repo_with_test_command(tmp_base, name, test_cmd, *, failing_test=True):
    repo = tmp_base / name
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest\n")
    body = "assert False" if failing_test else "assert True"
    (repo / "test_x.py").write_text(f"def test_x():\n    {body}\n")
    (repo / ".cleanroom.toml").write_text(
        '[cleanroom]\nimage = "python:3.12-slim"\n'
        'install = "pip install --target=.cleanroom-deps -r requirements.txt"\n'
        f'test = {json.dumps(test_cmd)}\n'
    )
    init_git_repo(repo)
    return repo


EFFECTIVELY_EMPTY_TEST_COMMANDS = {
    "comment-only": "#foo",
    "whitespace-plus-comment": "   # c",
    "unset-var-expansion": "$NOPE",
    "whitespace-only-expansion": "${X:-   }",
}


@requires_docker
@pytest.mark.parametrize("label,test_cmd", sorted(EFFECTIVELY_EMPTY_TEST_COMMANDS.items()))
def test_effectively_empty_test_command_with_failing_test_never_passes(tmp_base, label, test_cmd):
    # The smoking gun, parametrized across all four escape shapes: a
    # genuinely FAILING committed test, plus a test command that LOOKS
    # non-blank but the shell reduces to nothing. Must never exit 0, must
    # never report test: pass.
    repo = _repo_with_test_command(tmp_base, f"empty-{label}", test_cmd, failing_test=True)
    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)

    assert report.exit_code != 0, f"{label}: false green -- exit 0 despite a failing test and an effectively-empty command"
    test_step = report.get("test")
    assert test_step.status != "pass", f"{label}: false green -- test: pass despite a failing test and an effectively-empty command"
    assert test_step.status == "not-run-because-empty-command"


@requires_docker
def test_comment_only_command_cli_json_never_passes(tmp_base, capsys):
    # The same shape, through the real CLI, in --json mode.
    from cleanroom.cli import main

    repo = _repo_with_test_command(tmp_base, "comment-only-command", "#foo", failing_test=True)
    exit_code = main(["--repo", str(repo), "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert exit_code != 0
    assert payload["exit_code"] != 0
    test_status = next(s["status"] for s in payload["steps"] if s["name"] == "test")
    assert test_status != "pass"
    assert test_status == "not-run-because-empty-command"


@requires_docker
@pytest.mark.parametrize(
    "label,test_cmd,expect_pass",
    [
        ("true", "true", True),
        ("colon", ":", True),
        ("pipeline", "echo hi | cat", True),
        ("cd-and-ls", "cd / && ls", True),
        ("and-chain-short-circuits-to-false", "false && true", False),
    ],
)
def test_genuine_commands_are_not_over_rejected(tmp_base, label, test_cmd, expect_pass):
    # These must NOT be flagged empty -- they genuinely execute a program
    # and their real exit code must drive pass/fail exactly as before.
    # Over-rejecting a valid command is a new bug, not a fix.
    repo = _repo_with_test_command(tmp_base, f"genuine-{label}", test_cmd, failing_test=False)
    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)

    test_step = report.get("test")
    assert test_step.status != "not-run-because-empty-command", (
        f"{label}: over-rejected a command that genuinely executes"
    )
    if expect_pass:
        assert test_step.status == "pass", report.steps
        assert report.exit_code == 0
    else:
        assert test_step.status == "fail", report.steps
        assert report.exit_code != 0


@requires_docker
def test_real_failing_program_still_fails_not_reported_empty(tmp_base):
    # A genuinely failing, non-trivial command must still be reported fail
    # (with its real exit code), never confused with the empty-command path.
    repo = _repo_with_test_command(tmp_base, "real-failure-r4", "python3 -m pytest -q", failing_test=True)
    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)

    assert report.exit_code != 0
    test_step = report.get("test")
    assert test_step.status == "fail"
    assert test_step.status != "not-run-because-empty-command"


@requires_docker
def test_effectively_empty_install_command_never_passes(tmp_base):
    # Same escape, install side: install = "#nothing" must not silently
    # report install: pass either.
    repo = tmp_base / "empty-install-r4"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest\n")
    (repo / "t_test.py").write_text("def test_ok():\n    assert True\n")
    (repo / ".cleanroom.toml").write_text(
        '[cleanroom]\nimage = "python:3.12-slim"\n'
        'install = "#nothing"\n'
        'test = "true"\n'
    )
    init_git_repo(repo)
    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)

    install_step = report.get("install")
    assert install_step.status != "pass"
    assert install_step.status == "not-run-because-empty-command"


# --- Gate 2: >=2 genuinely clean fixtures exit 0, same run as gate 1 --------------

@requires_docker
@pytest.mark.parametrize("fixture_name", ["clean-python", "clean-node"])
def test_g2_clean_fixtures_pass(fixture_name, tmp_base):
    report = _run(fixture_name, tmp_base)
    assert report.exit_code == 0, report.steps
    assert report.all_pass()


# --- Gate 3: untracked-file dependence -- assert BOTH halves ----------------------

@requires_docker
def test_g3_untracked_file_fails_in_cleanroom_but_passes_in_worktree(tmp_base):
    repo = _materialized("untracked-file-dependency", tmp_base)

    # half A: cleanroom clones tracked-only -> the gitignored file is absent -> fail
    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)
    assert report.exit_code != 0
    test_step = report.get("test")
    assert test_step.status == "fail"
    assert test_step.cause_class == "untracked-file-dependency"

    # half B: the developer's own worktree still has the untracked file -> pass
    worktree = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert worktree.returncode == 0, worktree.stdout + worktree.stderr


def test_untracked_fixture_materializes_from_a_tracked_only_export(tmp_base):
    """The fixture must reproduce from a fresh clone, not from this machine.

    The worktree half of the gate above asserts that `secret.local` IS
    present -- and for a while that only held because the file happened to
    be sitting in the developer's checkout. It is gitignored, so it is not
    in HEAD, so a clean clone did not have it and the assertion could not
    pass there. Copying the fixture out of `git archive HEAD` is exactly
    what a fresh clone sees, so this fails anywhere the fixture depends on
    untracked local state, including on the machine that created it.
    """
    repo_root = Path(__file__).resolve().parents[1]
    if not (repo_root / ".git").exists():
        pytest.skip("not a git checkout; nothing to export")

    export = tmp_base / "tracked-only-export"
    export.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "HEAD", "--", "fixtures/untracked-file-dependency"],
        cwd=repo_root,
        capture_output=True,
    )
    assert archive.returncode == 0, archive.stderr.decode(errors="replace")
    subprocess.run(["tar", "-x", "-C", str(export)], input=archive.stdout, check=True)

    exported_fixture = export / "fixtures" / "untracked-file-dependency"
    assert not (exported_fixture / "secret.local").exists(), (
        "secret.local must NOT be tracked -- the fixture's entire point is a "
        "file that is present locally and absent from the repository"
    )

    materialized = materialize_git_repo(exported_fixture, tmp_base / "from-export")
    secret = materialized / "secret.local"
    assert secret.exists(), "materializing from a clean export must seed secret.local"
    assert secret.read_text() == "topsecret\n"

    tracked = subprocess.run(
        ["git", "ls-files", "secret.local"],
        cwd=materialized,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout.strip() == "", "secret.local must stay untracked in the materialized repo"


# --- Gate 4: path-depth assumption -- assert BOTH halves --------------------------

@requires_docker
def test_g4_path_depth_fails_shallow_passes_deep(tmp_base):
    repo = _materialized("path-depth-assumption", tmp_base)

    shallow_cfg = load_config(repo, overrides={"clone_root": "/r"})
    shallow_report = run_cleanroom(repo, shallow_cfg)
    assert shallow_report.exit_code != 0
    shallow_test = shallow_report.get("test")
    assert shallow_test.status == "fail"
    assert shallow_test.cause_class == "path-depth-assumption"

    deep_cfg = load_config(
        repo, overrides={"clone_root": "/very/deep/nested/checkout/path/for/ci/run"}
    )
    deep_report = run_cleanroom(repo, deep_cfg)
    assert deep_report.exit_code == 0, deep_report.steps
    assert deep_report.all_pass()


# --- Gate 5: root-only behaviour -- pass non-root, reported when run as root ------

@requires_docker
def test_g5_root_only_passes_nonroot_fails_root(tmp_base):
    repo = _materialized("root-only-permission-behaviour", tmp_base)
    cfg = load_config(repo)

    nonroot_report = run_cleanroom(repo, cfg, run_as_root=False)
    assert nonroot_report.exit_code == 0, nonroot_report.steps
    assert nonroot_report.all_pass()

    root_report = run_cleanroom(repo, cfg, run_as_root=True)
    assert root_report.exit_code != 0
    root_test = root_report.get("test")
    assert root_test.status == "fail"
    assert root_test.cause_class == "root-only-permission-behaviour"


# --- Gate 6: missing runtime -> not-run, never pass -- assert wording + exit ------

def test_g6_missing_runtime_reports_not_run_never_pass(monkeypatch, tmp_base):
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
    repo = _materialized("clean-python", tmp_base)
    cfg = load_config(repo)
    report = run_cleanroom(repo, cfg)

    assert report.exit_code != 0
    assert len(report.steps) == 3
    for step in report.steps:
        assert step.status == "not-run-because-no-container-runtime"
        assert step.status != "pass"


# --- F1 regression: the cleanroom repo must be self-contained. A fresh clone
# (no submodule init, nothing left behind on disk) must reproduce every
# fixture's failure/pass, exactly like this test file does against the
# working tree. This is the tool eating its own dog food. -------------------

@requires_docker
def test_f1_fresh_clone_reproduces_every_fixture(tmp_path):
    clone_dir = tmp_path / "fresh-clone"
    subprocess.run(
        ["git", "clone", "-q", str(REPO_ROOT), str(clone_dir)],
        check=True,
        capture_output=True,
    )

    cloned_fixtures = clone_dir / "fixtures"
    for name in list(DEFECT_FIXTURES) + ["root-only-permission-behaviour", "clean-python", "clean-node"]:
        fixture_dir = cloned_fixtures / name
        assert fixture_dir.is_dir(), f"{name}: missing from fresh clone"
        contents = list(fixture_dir.iterdir())
        assert contents, f"{name}: fresh clone produced an EMPTY fixture directory (F1 regression)"

    materialize_base = tmp_path / "materialized-from-clone"
    materialize_base.mkdir()

    # every defect fixture still reproduces its cause, sourced from the fresh clone
    for name, expected_cause in DEFECT_FIXTURES.items():
        repo = materialize_fixture(cloned_fixtures, name, materialize_base)
        cfg = load_config(repo)
        report = run_cleanroom(repo, cfg)
        assert report.exit_code != 0, f"{name}: expected failure from fresh clone"
        failing = [s for s in report.steps if s.status == "fail"]
        assert any(s.cause_class == expected_cause for s in failing), (
            f"{name} (from fresh clone): expected {expected_cause!r}, got "
            f"{[(s.name, s.cause_class) for s in failing]}"
        )

    # the root-only class, from the fresh clone
    repo = materialize_fixture(cloned_fixtures, "root-only-permission-behaviour", materialize_base)
    cfg = load_config(repo)
    root_report = run_cleanroom(repo, cfg, run_as_root=True)
    assert root_report.exit_code != 0
    root_test = root_report.get("test")
    assert root_test.cause_class == "root-only-permission-behaviour"

    # both clean fixtures pass, from the fresh clone
    for name in ("clean-python", "clean-node"):
        repo = materialize_fixture(cloned_fixtures, name, materialize_base)
        cfg = load_config(repo)
        report = run_cleanroom(repo, cfg)
        assert report.exit_code == 0, f"{name} (from fresh clone): expected pass, got {report.steps}"
        assert report.all_pass()
