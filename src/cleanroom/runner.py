"""Core orchestration: resolve config -> clone (tracked-only HEAD, shallow
path) -> docker run matching CI -> install (manifest-only) -> run declared
commands as non-root -> per-step report + exit code.

Every step reports exactly one of pass / fail / not-run-because-<reason>.
A step that did not run is never reported as pass.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .classify import classify, load_gitignore_patterns
from .config import Config
from .docker_runtime import docker_available

STEP_NAMES = ("clone", "install", "test")

# --- effective-empty detection --------------------------------------------
# A RAW-empty/whitespace-only/empty-list command is already rejected by
# config.py before it ever reaches here. But a command that is non-blank as
# TEXT can still reduce to NOTHING once the container's shell actually
# parses/expands it: a comment-only line (`#foo`), an unset variable
# expansion (`$NOPE`), a whitespace-only expansion (`${X:-   }`). Any of
# these executes as a shell no-op, exits 0, and would be reported `pass`
# with zero commands ever having run -- the exact false-green class this
# tool exists to prevent, just one layer deeper than a static string check
# can see. Static validation cannot catch this: it requires actually
# asking the shell what it did.
#
# Mechanism: run the declared command under `set -x` with a unique PS4
# marker, tee'd to a private trace file that never touches the real
# stdout/stderr the report is built from. Count trace lines where the
# marker is immediately followed by a non-space character -- a REAL
# command token, not an empty expansion. Zero such lines means the shell
# executed nothing. Portable to POSIX sh/dash (these base images' /bin/sh
# is dash, not bash) -- no BASH_XTRACEFD or other bashisms.
_TRACE_MARKER = "@@CLEANROOM_TRACE_f3a91c@@"
_REALCOUNT_MARKER = "@@CLEANROOM_REALCOUNT@@"
_REALCOUNT_RE = re.compile(re.escape(_REALCOUNT_MARKER) + r":(\d+)" + re.escape(_REALCOUNT_MARKER))


def _wrap_for_effective_empty_detection(declared_cmd: str) -> str:
    """Build the shell script actually passed to `sh -c` in place of the
    raw declared command. The declared command's own stdout is untouched.
    Its own genuine stderr is preserved too (re-emitted after filtering
    out only the trace-marker lines) -- cause classification still sees
    real error text like ModuleNotFoundError/Cannot find module. A trailer
    line carrying the real-command count is appended to stdout and MUST be
    stripped by the caller before using stdout for reporting/classification.

    Ordering matters: the bookkeeping (`rc=$?; set +x`) that runs right
    after the traced block must itself never be traced under the SAME
    marker (it would inflate the real-command count and defeat the whole
    check) -- grouping it into its own `{ ... } 2>/dev/null` block is what
    prevents that leak; a bare `rc=$? 2>/dev/null` on its own line does
    NOT suppress dash's trace-announcement for that line (verified
    in-container), only a grouped redirect does.

    Known limitation: a declared command that itself calls `exec` replaces
    the shell process, so this wrapper's own trailing bookkeeping never
    runs and the real-command marker is never emitted. The caller treats a
    missing marker as "detection inconclusive" and falls back to trusting
    the raw exit code -- safe (no false green: exec still runs something
    real and its exit code still surfaces normally), just loses the
    cleaned-stderr benefit for cause classification in that one edge case.
    """
    return (
        'TRACE="/tmp/.cr_trace_$$"; '
        f'export PS4="{_TRACE_MARKER}"; '
        f'{{ set -x; {declared_cmd}\n'
        f'}} 2>"$TRACE"; '
        '{ rc=$?; set +x; } 2>/dev/null; '
        f'grep -v "^{_TRACE_MARKER}" "$TRACE" >&2; '
        f'realcount=$(grep -c "^{_TRACE_MARKER}[^ ]" "$TRACE"); '
        'rm -f "$TRACE"; '
        f'printf "\\n{_REALCOUNT_MARKER}:%s{_REALCOUNT_MARKER}\\n" "$realcount"; '
        'exit $rc'
    )


def _strip_realcount_marker(stdout: str) -> tuple[str, Optional[int]]:
    """Returns (cleaned_stdout, realcount). realcount is None if the
    marker was never found (detection inconclusive -- see the `exec`
    limitation above)."""
    m = _REALCOUNT_RE.search(stdout)
    if not m:
        return stdout, None
    return _REALCOUNT_RE.sub("", stdout), int(m.group(1))


@dataclass
class StepResult:
    name: str
    status: str
    cause_class: Optional[str] = None
    output: str = ""


@dataclass
class Report:
    steps: list[StepResult]
    exit_code: int

    def all_pass(self) -> bool:
        return all(s.status == "pass" for s in self.steps)

    def get(self, name: str) -> Optional[StepResult]:
        for s in self.steps:
            if s.name == name:
                return s
        return None


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run, but OSError (E2BIG argument-list-too-long from an
    oversized declared command, ENOENT missing binary, etc.) is never
    allowed to propagate as an uncaught crash -- every callsite here
    already handles a nonzero returncode, so an OSError is folded into a
    synthetic failed CompletedProcess rather than needing its own
    try/except at every callsite."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except OSError as e:
        return subprocess.CompletedProcess(
            cmd, returncode=126, stdout="", stderr=f"{type(e).__name__}: {e}"
        )


def _run_binary(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Same OSError-safety as _run(), but binary mode -- used for the git
    archive/docker cp tar stream, which must never be decoded as text."""
    try:
        return subprocess.run(cmd, capture_output=True, **kwargs)
    except OSError as e:
        return subprocess.CompletedProcess(
            cmd, returncode=126, stdout=b"", stderr=f"{type(e).__name__}: {e}".encode()
        )


def _skip_report(reason: str, from_step: str) -> Report:
    steps = []
    for name in STEP_NAMES:
        if name == from_step:
            steps.append(StepResult(name, "fail", "unclassified"))
        elif STEP_NAMES.index(name) > STEP_NAMES.index(from_step):
            steps.append(StepResult(name, f"not-run-because-{reason}"))
    return Report(steps=steps, exit_code=1)


def not_run_report(reason: str) -> Report:
    """Every step reports the same not-run-because-<reason> outcome. Used
    for whole-run preconditions that fail before any step can meaningfully
    start (missing runtime, malformed config) -- never a silent pass."""
    steps = [StepResult(name, f"not-run-because-{reason}") for name in STEP_NAMES]
    return Report(steps=steps, exit_code=1)


def run_cleanroom(repo_path: Path, cfg: Config, *, run_as_root: bool = False) -> Report:
    repo_path = Path(repo_path)

    if not docker_available():
        return not_run_report("no-container-runtime")

    uid_gid = "0:0" if run_as_root else cfg.non_root_uid
    container_name = f"cleanroom-{uuid.uuid4().hex[:12]}"
    clone_root = cfg.clone_root

    create = _run(["docker", "create", "--name", container_name, "-w", clone_root,
                   cfg.image, "sleep", "infinity"])
    if create.returncode != 0:
        return _skip_report("clone-failed", "clone")

    try:
        start = _run(["docker", "start", container_name])
        if start.returncode != 0:
            return _skip_report("clone-failed", "clone")

        # --- clone: tracked-only HEAD, streamed as a tar (git archive) into
        # a deliberately shallow in-container path -------------------------
        mkdir_res = _run(["docker", "exec", "-u", "0:0", container_name, "mkdir", "-p", clone_root])
        archive = _run_binary(["git", "archive", "HEAD"], cwd=repo_path)
        if archive.returncode != 0 or mkdir_res.returncode != 0:
            return _skip_report("clone-failed", "clone")

        cp = _run_binary(["docker", "cp", "-", f"{container_name}:{clone_root}"], input=archive.stdout)
        chown = _run(["docker", "exec", "-u", "0:0", container_name, "chown", "-R", uid_gid, clone_root])
        if cp.returncode != 0 or chown.returncode != 0:
            return _skip_report("clone-failed", "clone")

        steps: list[StepResult] = [StepResult("clone", "pass")]
        gitignore_patterns = load_gitignore_patterns(repo_path)

        # --- install: the repo's own declared manifest only, as non-root --
        # Defensive guard (belt and suspenders on top of config.py's own
        # validation): a blank/whitespace-only resolved command is NEVER
        # executed as a `sh -c ""` no-op and reported pass -- that is a
        # silent false green. This is the last choke point before
        # anything reaches subprocess argv, so no future config path can
        # leak one through, even if config.py's own validation is ever
        # bypassed (e.g. an override supplied programmatically).
        if cfg.install and cfg.install.strip():
            install_wrapped = _wrap_for_effective_empty_detection(cfg.install)
            install_res = _run(["docker", "exec", "-u", uid_gid, "-w", clone_root,
                                 container_name, "sh", "-c", install_wrapped])
            clean_stdout, install_realcount = _strip_realcount_marker(install_res.stdout)
            output = clean_stdout + install_res.stderr
            if install_realcount == 0:
                # Effectively empty: non-blank as TEXT, but the shell
                # expanded/parsed it down to nothing (comment-only, unset
                # var, whitespace-only expansion) and ran zero real
                # commands. Never pass -- same reason as the static
                # raw-empty guard, one layer deeper.
                steps.append(StepResult("install", "not-run-because-empty-command"))
            elif install_res.returncode == 0:
                steps.append(StepResult("install", "pass"))
            else:
                cause = classify("install", output, run_as_root=run_as_root,
                                  gitignore_patterns=gitignore_patterns)
                steps.append(StepResult("install", "fail", cause, output))
                steps.append(StepResult("test", "not-run-because-install-failed"))
                return Report(steps=steps, exit_code=1)
        else:
            steps.append(StepResult("install", "not-run-because-empty-command"))

        # --- test: the repo's own declared command, as non-root -----------
        if not cfg.test or not cfg.test.strip():
            steps.append(StepResult("test", "not-run-because-empty-command"))
            exit_code = 0 if all(s.status == "pass" for s in steps) else 1
            return Report(steps=steps, exit_code=exit_code)

        test_wrapped = _wrap_for_effective_empty_detection(cfg.test)
        test_cmd = ["docker", "exec", "-u", uid_gid, "-w", clone_root]
        if cfg.language == "python":
            test_cmd += ["-e", f"PYTHONPATH={clone_root}/.cleanroom-deps"]
        test_cmd += [container_name, "sh", "-c", test_wrapped]
        test_res = _run(test_cmd)
        clean_stdout, test_realcount = _strip_realcount_marker(test_res.stdout)
        output = clean_stdout + test_res.stderr
        if test_realcount == 0:
            steps.append(StepResult("test", "not-run-because-empty-command"))
        elif test_res.returncode == 0:
            steps.append(StepResult("test", "pass"))
        else:
            cause = classify("test", output, run_as_root=run_as_root,
                              gitignore_patterns=gitignore_patterns)
            steps.append(StepResult("test", "fail", cause, output))
    finally:
        _run(["docker", "rm", "-f", container_name])

    exit_code = 0 if all(s.status == "pass" for s in steps) else 1
    return Report(steps=steps, exit_code=exit_code)
