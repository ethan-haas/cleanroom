import json
import os

import pytest

from cleanroom.detect import infer_project_config


def test_infers_python_from_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest\n")
    cfg = infer_project_config(tmp_path)
    assert cfg.language == "python"
    assert cfg.image == "python:3.12-slim"
    assert "requirements.txt" in cfg.install
    assert cfg.test == "python -m pytest -q"


def test_infers_python_version_from_workflow(tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest\n")
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(
        "jobs:\n  test:\n    strategy:\n      matrix:\n        python-version: ['3.11']\n"
    )
    cfg = infer_project_config(tmp_path)
    assert cfg.image == "python:3.11-slim"


def test_infers_node_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "node test.js"}}')
    (tmp_path / "package-lock.json").write_text("{}")
    cfg = infer_project_config(tmp_path)
    assert cfg.language == "node"
    assert cfg.install == "npm ci"
    assert cfg.test == "npm test"


def test_node_without_lockfile_uses_npm_install(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    cfg = infer_project_config(tmp_path)
    assert cfg.install == "npm install"


def test_node_version_from_workflow(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text("node-version: '18'\n")
    cfg = infer_project_config(tmp_path)
    assert cfg.image == "node:18-slim"


def test_unknown_language_when_no_manifest(tmp_path):
    cfg = infer_project_config(tmp_path)
    assert cfg.language == "unknown"


def test_load_config_honours_cleanroom_toml(tmp_path):
    from cleanroom.config import load_config

    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / ".cleanroom.toml").write_text(
        '[cleanroom]\nimage = "python:3.9-slim"\ntest = "python -m pytest -x"\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.image == "python:3.9-slim"
    assert cfg.test == "python -m pytest -x"
    assert cfg.source == "config"


def test_load_config_falls_back_to_inference_when_absent(tmp_path):
    from cleanroom.config import load_config

    (tmp_path / "requirements.txt").write_text("pytest\n")
    cfg = load_config(tmp_path)
    assert cfg.source == "inferred"
    assert cfg.image == "python:3.12-slim"


# --- E4 regression: malformed .cleanroom.toml must never raise a raw
# tomllib traceback -- it is a structured, reportable outcome. ------------

def test_malformed_cleanroom_toml_raises_configerror_not_raw_traceback(tmp_path):
    from cleanroom.config import ConfigError, load_config

    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / ".cleanroom.toml").write_text("this is [ not valid toml at all\n")

    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_cli_reports_malformed_config_gracefully_and_exits_nonzero(tmp_path, capsys):
    from cleanroom.cli import main

    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / ".cleanroom.toml").write_text("this is [ not valid toml at all\n")

    exit_code = main(["--repo", str(tmp_path)])
    out = capsys.readouterr()

    assert exit_code != 0
    combined = out.out + out.err
    assert "Traceback" not in combined
    assert "not-run-because-malformed-config" in combined
    assert "pass" not in out.out.lower()


def test_cli_reports_malformed_config_gracefully_as_json(tmp_path, capsys):
    from cleanroom.cli import main

    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / ".cleanroom.toml").write_text("this is [ not valid toml at all\n")

    exit_code = main(["--repo", str(tmp_path), "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert exit_code != 0
    assert payload["exit_code"] != 0
    assert all(s["status"] == "not-run-because-malformed-config" for s in payload["steps"])
    assert all(s["status"] != "pass" for s in payload["steps"])
    assert "error" in payload


# --- A TOMLDecodeError-only guard is incomplete. Five
# other malformed-config shapes still crashed with raw tracebacks (some
# deep inside subprocess argv construction, well after parsing). Every one
# of these must raise ConfigError from load_config() -- never a bare
# TypeError/OSError/UnicodeDecodeError -- and every one must survive the
# full CLI round-trip (including --json validity). --------------------------

def _repo_with_toml(tmp_path, toml_text=None, toml_bytes=None):
    (tmp_path / "requirements.txt").write_text("pytest\n")
    cfg_path = tmp_path / ".cleanroom.toml"
    if toml_bytes is not None:
        cfg_path.write_bytes(toml_bytes)
    else:
        cfg_path.write_text(toml_text)
    return tmp_path


@pytest.mark.parametrize(
    "toml_text,bad_key",
    [
        pytest.param("[cleanroom]\nimage = 123\n", "image", id="image-int"),
        pytest.param("[cleanroom]\nimage = [1, 2]\n", "image", id="image-list-of-int"),
        pytest.param("[cleanroom]\ninstall = 123\n", "install", id="install-int"),
    ],
)
def test_wrong_type_config_value_raises_configerror_not_typeerror(tmp_path, toml_text, bad_key):
    from cleanroom.config import ConfigError, load_config

    repo = _repo_with_toml(tmp_path, toml_text=toml_text)
    with pytest.raises(ConfigError) as excinfo:
        load_config(repo)
    assert bad_key in str(excinfo.value)


def test_config_list_of_str_command_is_accepted_and_joined(tmp_path):
    from cleanroom.config import load_config

    repo = _repo_with_toml(tmp_path, toml_text='[cleanroom]\ninstall = ["pip", "install", "-r", "requirements.txt"]\n')
    cfg = load_config(repo)
    assert cfg.install == "pip install -r requirements.txt"


def test_non_utf8_cleanroom_toml_raises_configerror_not_unicodedecodeerror(tmp_path):
    from cleanroom.config import ConfigError, load_config

    repo = _repo_with_toml(tmp_path, toml_bytes=b"\xff\xfe[cleanroom]\nimage")
    with pytest.raises(ConfigError):
        load_config(repo)


def test_cleanroom_toml_as_directory_raises_configerror_not_isadirectoryerror(tmp_path):
    from cleanroom.config import ConfigError, load_config

    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / ".cleanroom.toml").mkdir()
    with pytest.raises(ConfigError):
        load_config(tmp_path)


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission bits do not block a non-posix OS or root",
)
def test_unreadable_cleanroom_toml_raises_configerror_not_permissionerror(tmp_path):
    from cleanroom.config import ConfigError, load_config

    repo = _repo_with_toml(tmp_path, toml_text='[cleanroom]\nimage = "python:3.12-slim"\n')
    cfg_path = repo / ".cleanroom.toml"
    cfg_path.chmod(0o000)
    try:
        with pytest.raises(ConfigError):
            load_config(repo)
    finally:
        cfg_path.chmod(0o644)


def test_cli_wrong_type_config_value_is_valid_json_never_traceback(tmp_path, capsys):
    from cleanroom.cli import main

    repo = _repo_with_toml(tmp_path, toml_text="[cleanroom]\nimage = 123\n")
    exit_code = main(["--repo", str(repo), "--json"])
    out = capsys.readouterr()

    assert exit_code != 0
    assert "Traceback" not in (out.out + out.err)
    payload = json.loads(out.out)  # must parse -- this is the whole point
    assert payload["exit_code"] != 0
    assert all(s["status"] == "not-run-because-malformed-config" for s in payload["steps"])
    assert all(s["status"] != "pass" for s in payload["steps"])


def test_cli_directory_config_is_valid_json_never_traceback(tmp_path, capsys):
    from cleanroom.cli import main

    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / ".cleanroom.toml").mkdir()

    exit_code = main(["--repo", str(tmp_path), "--json"])
    out = capsys.readouterr()

    assert exit_code != 0
    assert "Traceback" not in (out.out + out.err)
    payload = json.loads(out.out)
    assert payload["exit_code"] != 0
    assert all(s["status"] == "not-run-because-malformed-config" for s in payload["steps"])
    assert all(s["status"] != "pass" for s in payload["steps"])


# --- An empty/blank/empty-list command must
# NEVER produce a false green. `test = ""` previously executed `sh -c ""`
# (a silent no-op that exits 0) and reported test: pass -- meaning a repo
# with a genuinely FAILING test suite and `test = ""` came back green.
# `install = ""` shared the same root cause. -------------------------------

@pytest.mark.parametrize(
    "field,toml_text",
    [
        pytest.param("test", '[cleanroom]\ntest = ""\n', id="test-empty-string"),
        pytest.param("test", '[cleanroom]\ntest = "   "\n', id="test-whitespace-only"),
        pytest.param("test", "[cleanroom]\ntest = []\n", id="test-empty-list"),
        pytest.param("install", '[cleanroom]\ninstall = ""\n', id="install-empty-string"),
        pytest.param("install", '[cleanroom]\ninstall = "   "\n', id="install-whitespace-only"),
        pytest.param("install", "[cleanroom]\ninstall = []\n", id="install-empty-list"),
    ],
)
def test_blank_command_field_raises_configerror_not_silently_accepted(tmp_path, field, toml_text):
    from cleanroom.config import ConfigError, load_config

    repo = _repo_with_toml(tmp_path, toml_text=toml_text)
    with pytest.raises(ConfigError) as excinfo:
        load_config(repo)
    assert field in str(excinfo.value)


def test_blank_list_form_bracket_string_is_not_special_cased(tmp_path):
    # [""] / ["  "] are NOT the false-green shape: shlex-quoting turns them
    # into a real (failing) shell invocation of an empty-named command, not
    # a silent no-op -- these must keep working exactly as before.
    from cleanroom.config import load_config

    repo = _repo_with_toml(tmp_path, toml_text='[cleanroom]\ntest = [""]\n')
    cfg = load_config(repo)
    assert cfg.test == "''"
    assert cfg.test.strip()  # non-blank as a literal string -- not rejected


def test_cli_blank_command_field_is_valid_json_never_traceback(tmp_path, capsys):
    from cleanroom.cli import main

    repo = _repo_with_toml(tmp_path, toml_text='[cleanroom]\ntest = ""\n')
    exit_code = main(["--repo", str(repo), "--json"])
    out = capsys.readouterr()

    assert exit_code != 0
    assert "Traceback" not in (out.out + out.err)
    payload = json.loads(out.out)
    assert payload["exit_code"] != 0
    assert all(s["status"] == "not-run-because-malformed-config" for s in payload["steps"])
    assert all(s["status"] != "pass" for s in payload["steps"])


def test_runner_reports_not_run_when_docker_unavailable_for_blank_command_config(monkeypatch):
    # Cheap unit-level sanity check only: proves a hand-built blank-test
    # Config (bypassing load_config's own validation entirely) still never
    # comes back `pass` even in the earliest possible short-circuit path.
    # The REAL belt-and-suspenders proof -- that the runner's own
    # cfg.test.strip() guard (not config.py) is what stops the false green
    # when docker IS available and the pipeline actually runs -- is
    # test_gates.py::test_a_runner_defensive_guard_blank_test_never_passes.
    from cleanroom.config import Config
    from cleanroom.runner import run_cleanroom

    monkeypatch.setattr("cleanroom.runner.docker_available", lambda: False)
    cfg = Config(image="python:3.12-slim", install="pip install -r requirements.txt", test="")
    report = run_cleanroom("/nonexistent", cfg)
    assert report.exit_code != 0
    assert all(s.status != "pass" for s in report.steps)


# --- A config file the tool cannot place must fail closed, never be skipped
# past. `raw.get("cleanroom", raw)` used to ignore any table that was not
# literally [cleanroom], so a file written as [project] or [tool.cleanroom]
# contributed nothing: every setting fell back to inference and the run
# reported on commands the user never declared, with no warning. ----------

@pytest.mark.parametrize(
    "label,toml_text",
    [
        pytest.param(
            "project-section",
            '[project]\nimage = "python:3.11-slim"\ntest = "pytest -q"\n',
            id="project-section",
        ),
        pytest.param(
            "tool-cleanroom-section",
            '[tool.cleanroom]\nimage = "python:3.11-slim"\ntest = "pytest -q"\n',
            id="tool-cleanroom-section",
        ),
        pytest.param(
            "typo-key-inside-table",
            '[cleanroom]\nimagee = "python:3.11-slim"\n',
            id="typo-key-inside-table",
        ),
        pytest.param(
            "stray-key-beside-table",
            '[cleanroom]\nimage = "python:3.11-slim"\n\n[other]\nx = 1\n',
            id="stray-table-beside-cleanroom",
        ),
        pytest.param(
            "typo-key-bare-form",
            'imagee = "python:3.11-slim"\n',
            id="typo-key-bare-form",
        ),
    ],
)
def test_unrecognized_config_keys_are_rejected_not_silently_ignored(tmp_path, label, toml_text):
    from cleanroom.config import ConfigError, load_config

    repo = _repo_with_toml(tmp_path, toml_text=toml_text)
    with pytest.raises(ConfigError) as excinfo:
        load_config(repo)
    # The message has to name what it could not place, or it is not actionable.
    message = str(excinfo.value)
    assert "unrecognized key" in message


def test_cli_unrecognized_config_section_is_malformed_config_never_pass(tmp_path, capsys):
    # The end-to-end shape: a config the tool cannot read must surface as
    # not-run-because-malformed-config with a non-zero exit -- never as a
    # green run against silently-inferred commands.
    import json

    from cleanroom.cli import main

    repo = _repo_with_toml(
        tmp_path, toml_text='[project]\ntest = "exit 1"\n'
    )
    code = main(["--repo", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code != 0
    assert [s["status"] for s in payload["steps"]] == [
        "not-run-because-malformed-config"
    ] * len(payload["steps"])
    assert not any(s["status"] == "pass" for s in payload["steps"])


# --- Over-rejection controls: the guard above must not reject config that
# was always valid. Without these, "reject everything" would pass. --------

def test_valid_cleanroom_table_still_loads(tmp_path):
    from cleanroom.config import load_config

    repo = _repo_with_toml(
        tmp_path,
        toml_text=(
            '[cleanroom]\nimage = "python:3.12-slim"\ninstall = "pip install -r requirements.txt"\n'
            'test = "python -m pytest -q"\nclone_root = "/r"\nnon_root_uid = "1000:1000"\n'
        ),
    )
    cfg = load_config(repo)
    assert cfg.image == "python:3.12-slim"
    assert cfg.test == "python -m pytest -q"


def test_valid_bare_top_level_keys_still_load(tmp_path):
    # The documented alternative to a [cleanroom] table.
    from cleanroom.config import load_config

    repo = _repo_with_toml(
        tmp_path, toml_text='image = "python:3.12-slim"\ntest = "python -m pytest -q"\n'
    )
    cfg = load_config(repo)
    assert cfg.image == "python:3.12-slim"
    assert cfg.test == "python -m pytest -q"


def test_empty_config_file_falls_back_to_inference(tmp_path):
    # An empty file declares nothing; inferring everything is correct and
    # must not be turned into an error by the unknown-key guard.
    from cleanroom.config import load_config

    repo = _repo_with_toml(tmp_path, toml_text="")
    cfg = load_config(repo)
    assert cfg.image
    assert cfg.test
