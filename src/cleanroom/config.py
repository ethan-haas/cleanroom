"""Resolve a run configuration: .cleanroom.toml overrides layered on top of
sane inference from the repo's own manifest (see detect.py). Absence of the
config file is a supported, sane default -- not an error."""

from __future__ import annotations

import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .detect import infer_project_config

DEFAULT_CLONE_ROOT = "/r"
DEFAULT_NON_ROOT_UID = "1000:1000"


class ConfigError(Exception):
    """.cleanroom.toml exists but could not be read, decoded, parsed, or
    type-validated. Covers every shape of "the file on disk is not usable
    config": a directory where a file was expected, unreadable permissions,
    non-UTF8 bytes, invalid TOML syntax, and a value of the wrong type
    (e.g. `image = 123`) that would otherwise reach a subprocess argv as a
    non-str and crash there instead. Never let any of this surface as a
    raw traceback -- callers must turn it into a structured
    not-run-because-malformed-config outcome, never a silent pass."""


def _require_str(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"malformed .cleanroom.toml: '{name}' must be a non-empty string, "
            f"got {type(value).__name__}: {value!r}"
        )
    return value


def _require_command(name: str, value: object) -> str:
    """A command field (install/test/...) must be a non-blank string, or a
    non-empty list of strings (argv-style), joined into the single shell
    command string the runner actually executes.

    A bare empty string, a whitespace-only string, or an empty list are all
    REJECTED here -- not treated as "no command declared". An empty command
    executed via `sh -c ""` is a silent no-op that exits 0: the runner
    would report that step `pass` without ever having run it, which is
    exactly the false-green this tool exists to prevent (a repo with a
    genuinely failing test suite and `test = ""` must never come back
    green). Non-empty-but-blank list forms like `[""]`/`["  "]` are
    deliberately NOT special-cased here: shlex-quoting turns them into a
    real (failing) shell invocation of an empty-named command, not a
    silent no-op, so they already fail loudly on their own."""
    if isinstance(value, str):
        result = value
    elif isinstance(value, list):
        if not value:
            raise ConfigError(
                f"malformed .cleanroom.toml: '{name}' must not be an empty list "
                f"-- an empty command silently reports the step as passed "
                f"without ever running it"
            )
        if not all(isinstance(x, str) for x in value):
            raise ConfigError(
                f"malformed .cleanroom.toml: '{name}' list must contain only "
                f"strings, got {value!r}"
            )
        result = " ".join(shlex.quote(x) for x in value)
    else:
        raise ConfigError(
            f"malformed .cleanroom.toml: '{name}' must be a string or a list of "
            f"strings, got {type(value).__name__}: {value!r}"
        )

    if not result.strip():
        raise ConfigError(
            f"malformed .cleanroom.toml: '{name}' must not be empty or "
            f"whitespace-only -- an empty command silently reports the step "
            f"as passed without ever running it"
        )
    return result


KNOWN_KEYS = ("image", "install", "test", "clone_root", "non_root_uid")


def _reject_unknown_keys(data: dict, where: str) -> None:
    """A key this tool does not recognize is an ERROR, never something to
    skip past.

    Previously any table that was not literally `[cleanroom]` was ignored
    wholesale: `raw.get("cleanroom", raw)` meant a file written as
    `[project]` or `[tool.cleanroom]` -- both entirely natural guesses
    for anyone used to pyproject.toml -- silently contributed nothing.
    Every setting quietly fell back
    to inference and the run reported success, so the user was told their
    repository passed while the commands they declared had never been
    read. That is the same failure this tool exists to catch, one level up:
    reporting a green result for something other than what was asked for.
    So it fails closed, like every other unusable config shape here, and
    names the keys it could not place."""
    unknown = sorted(k for k in data if k not in KNOWN_KEYS)
    if unknown:
        raise ConfigError(
            f"malformed .cleanroom.toml: unrecognized key(s) at {where}: "
            f"{', '.join(repr(k) for k in unknown)}. Valid keys are "
            f"{', '.join(KNOWN_KEYS)}, either under a [cleanroom] table or "
            f"at the top level. (Note: [tool.cleanroom] and [project] are "
            f"not read -- this file is not pyproject.toml.)"
        )


@dataclass
class Config:
    image: str
    install: str
    test: str
    clone_root: str = DEFAULT_CLONE_ROOT
    non_root_uid: str = DEFAULT_NON_ROOT_UID
    language: str = "unknown"
    source: str = "inferred"  # "inferred" | "config" (.cleanroom.toml present)


def load_config(repo_path: Path, overrides: Optional[dict] = None) -> Config:
    repo_path = Path(repo_path)
    inferred = infer_project_config(repo_path)

    cfg_path = repo_path / ".cleanroom.toml"
    data: dict = {}
    # exists() itself can raise on a genuinely broken path (e.g. a symlink
    # loop) -- guard it too rather than assume it's infallible.
    try:
        has_file = cfg_path.exists()
    except OSError as e:
        raise ConfigError(f"cannot access .cleanroom.toml: {e}") from e

    if has_file:
        # Every way "the bytes on disk are not usable TOML" can fail:
        # IsADirectoryError/PermissionError/FileNotFoundError (all OSError),
        # non-UTF8 content (UnicodeDecodeError, raised by tomllib's own
        # internal decode), and invalid TOML syntax (TOMLDecodeError). None
        # of these may ever reach the caller as a raw traceback.
        try:
            with cfg_path.open("rb") as f:
                raw = tomllib.load(f)
        except OSError as e:
            raise ConfigError(f"cannot read .cleanroom.toml: {e}") from e
        except UnicodeDecodeError as e:
            raise ConfigError(f"malformed .cleanroom.toml: not valid UTF-8 ({e})") from e
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"malformed .cleanroom.toml: {e}") from e

        # allow either a [cleanroom] table or bare top-level keys
        if "cleanroom" in raw:
            data = raw["cleanroom"]
            if not isinstance(data, dict):
                raise ConfigError(
                    "malformed .cleanroom.toml: '[cleanroom]' must be a table, got "
                    f"{type(data).__name__}: {data!r}"
                )
            _reject_unknown_keys(data, "[cleanroom]")
            _reject_unknown_keys(
                {k: v for k, v in raw.items() if k != "cleanroom"},
                "top level (alongside [cleanroom])",
            )
        else:
            data = raw
            _reject_unknown_keys(data, "top level")

    cfg = Config(
        image=_require_str("image", data["image"]) if "image" in data else inferred.image,
        install=_require_command("install", data["install"]) if "install" in data else inferred.install,
        test=_require_command("test", data["test"]) if "test" in data else inferred.test,
        clone_root=_require_str("clone_root", data["clone_root"]) if "clone_root" in data else DEFAULT_CLONE_ROOT,
        non_root_uid=_require_str("non_root_uid", data["non_root_uid"]) if "non_root_uid" in data else DEFAULT_NON_ROOT_UID,
        language=inferred.language,
        source="config" if has_file else "inferred",
    )

    if overrides:
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)

    return cfg
