"""Language detection and sane-default inference when .cleanroom.toml is absent.

Looks at the repo's own manifest (requirements.txt / pyproject.toml for Python,
package.json / package-lock.json for Node) and its declared CI
(.github/workflows/*.yml) to pick a container image and install/test commands.
The interface is intentionally narrow (one dataclass in, one out) so more
languages can be added without touching the runner.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PY_VERSION = "3.12"
DEFAULT_NODE_VERSION = "20"

_PY_VERSION_RE = re.compile(r"python-version:\s*\[?['\"]?([\d.]+)")
_NODE_VERSION_RE = re.compile(r"node-version:\s*\[?['\"]?([\d.]+)")


@dataclass
class InferredConfig:
    language: str  # "python" | "node" | "unknown"
    image: str
    install: str
    test: str


def _workflow_versions(repo_path: Path) -> dict:
    """Scan .github/workflows/*.yml for python-version:/node-version: hints.

    Deliberately regex-based (no YAML dependency) since we only need one
    scalar value out of files we do not otherwise need to understand.
    """
    versions = {"python": None, "node": None}
    wf_dir = repo_path / ".github" / "workflows"
    if not wf_dir.is_dir():
        return versions
    for f in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        if versions["python"] is None:
            m = _PY_VERSION_RE.search(text)
            if m:
                versions["python"] = m.group(1)
        if versions["node"] is None:
            m = _NODE_VERSION_RE.search(text)
            if m:
                versions["node"] = m.group(1)
    return versions


def infer_project_config(repo_path: Path) -> InferredConfig:
    repo_path = Path(repo_path)
    wf = _workflow_versions(repo_path)

    package_json = repo_path / "package.json"
    requirements_txt = repo_path / "requirements.txt"
    pyproject_toml = repo_path / "pyproject.toml"

    if package_json.exists():
        node_version = wf["node"]
        scripts = {}
        try:
            data = json.loads(package_json.read_text())
            scripts = data.get("scripts", {}) or {}
            if node_version is None:
                engines_node = (data.get("engines", {}) or {}).get("node", "") or ""
                m = re.search(r"([\d]+(?:\.[\d]+)?)", engines_node)
                if m:
                    node_version = m.group(1)
        except (OSError, json.JSONDecodeError):
            pass
        node_version = node_version or DEFAULT_NODE_VERSION
        image = f"node:{node_version}-slim"
        install = "npm ci" if (repo_path / "package-lock.json").exists() else "npm install"
        test = "npm test" if "test" in scripts else "node test.js"
        return InferredConfig("node", image, install, test)

    if requirements_txt.exists() or pyproject_toml.exists():
        py_version = wf["python"]
        if py_version is None and pyproject_toml.exists():
            try:
                with pyproject_toml.open("rb") as f:
                    data = tomllib.load(f)
                req = (data.get("project", {}) or {}).get("requires-python", "") or ""
                m = re.search(r"([\d]+\.[\d]+)", req)
                if m:
                    py_version = m.group(1)
            except (OSError, tomllib.TOMLDecodeError):
                pass
        py_version = py_version or DEFAULT_PY_VERSION
        image = f"python:{py_version}-slim"
        if requirements_txt.exists():
            install = "pip install --target=.cleanroom-deps -r requirements.txt"
        else:
            install = "pip install --target=.cleanroom-deps ."
        test = "python -m pytest -q"
        return InferredConfig("python", image, install, test)

    return InferredConfig("unknown", f"python:{DEFAULT_PY_VERSION}-slim", "", "python -m pytest -q")
