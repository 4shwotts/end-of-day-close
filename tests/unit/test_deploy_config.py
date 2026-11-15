"""Checks on the Dockerfile, the Compose file and the CI workflow.

These do not build or run anything; they parse the files and assert the
properties the deployment relies on, so a careless edit (dropping the
non-root user, the health check or the Compose hardening) fails in CI rather
than on the live host.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from eod_close.web.__main__ import DEFAULT_PORT, port_from_environment

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def load_yaml(relative: str) -> Any:
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def final_stage(dockerfile: str) -> str:
    return dockerfile.rsplit("\nFROM ", 1)[1]


def test_dockerfile_is_multi_stage_on_a_pinned_slim_base() -> None:
    bases = re.findall(r"^FROM (\S+)", DOCKERFILE, flags=re.MULTILINE)
    assert len(bases) >= 2
    assert all(base.startswith("python:3.13-slim") for base in bases)
    assert not any(base.endswith(":latest") for base in bases)


def test_runtime_stage_runs_as_a_non_root_user() -> None:
    users = re.findall(r"^USER (\S+)", final_stage(DOCKERFILE), flags=re.MULTILINE)
    assert users, "the runtime stage must switch user"
    uid = users[-1].split(":")[0]
    assert uid not in {"0", "root"}


def test_runtime_stage_has_a_health_check_and_exec_form_command() -> None:
    runtime = final_stage(DOCKERFILE)
    assert "HEALTHCHECK" in runtime
    assert "/api/health" in runtime
    assert re.search(r'^CMD \["python", "-m", "eod_close.web"\]', runtime, flags=re.MULTILINE)


def test_docker_ignore_keeps_local_state_out_of_the_image() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for path in [".git", ".venv", "tests", "cases"]:
        assert path in ignored


def test_compose_is_hardened_and_bound_to_localhost() -> None:
    web = load_yaml("compose.yaml")["services"]["web"]
    assert web["read_only"] is True
    assert "ALL" in web["cap_drop"]
    assert "no-new-privileges:true" in web["security_opt"]
    assert all(str(port).startswith("127.0.0.1:") for port in web["ports"])


@pytest.mark.parametrize(
    ("environ", "expected"),
    [({}, DEFAULT_PORT), ({"PORT": ""}, DEFAULT_PORT), ({"PORT": "9000"}, 9000)],
)
def test_port_comes_from_the_environment(environ: dict[str, str], expected: int) -> None:
    assert port_from_environment(environ) == expected


@pytest.mark.parametrize("value", ["0", "70000", "http"])
def test_invalid_port_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match=r"PORT|invalid literal"):
        port_from_environment({"PORT": value})


def workflow() -> dict[Any, Any]:
    loaded: dict[Any, Any] = load_yaml(".github/workflows/ci.yml")
    return loaded


def test_workflow_defaults_to_read_only_permissions() -> None:
    assert workflow()["permissions"] == {"contents": "read"}


def test_workflow_python_matches_the_project() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = workflow()["env"]["PYTHON_VERSION"]
    assert f'requires-python = ">={version}"' in pyproject
