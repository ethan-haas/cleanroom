"""Container runtime availability check. Missing runtime is a first-class,
honestly-reported outcome -- never silently treated as pass."""

from __future__ import annotations

import subprocess


def docker_available(timeout: float = 6.0) -> bool:
    """True iff `docker info` succeeds against whatever DOCKER_HOST (or the
    default local socket) is currently configured in the environment. A
    bogus DOCKER_HOST must fail fast, not hang -- hence the short timeout."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return result.returncode == 0
