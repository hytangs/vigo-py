from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERSION = "0.3.0"
API_VERSION = "1.0"
CITY_FORMAT_VERSION = 1
RESULT_SCHEMA_VERSION = 1
PUBLIC_COMMANDS = ("build", "capabilities", "inspect", "route", "matrix", "reach", "compare")


class VigoError(RuntimeError):
    """A VIGO operation could not be completed."""


class VigoTimeoutError(VigoError, TimeoutError):
    """A VIGO operation exceeded its caller-supplied time limit."""


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    """The compatible VIGO runtime selected for this Python process."""

    product_version: str
    api_version: str
    source: str
    path: str
    command: tuple[str, ...] = field(repr=False)
    capabilities: Mapping[str, Any] = field(repr=False)


def _app_command(app: Path) -> tuple[str, ...] | None:
    contents = app.expanduser().resolve() / "Contents"
    executable = contents / "MacOS" / "VIGO Studio"
    program = contents / "Resources" / "app" / "public" / "vigo.mjs"
    if executable.is_file() and program.is_file():
        return (str(executable), str(program))
    return None


def _command_environment(command: Sequence[str]) -> dict[str, str]:
    environment = os.environ.copy()
    executable = Path(command[0])
    if executable.parent.name == "MacOS" and executable.parent.parent.name == "Contents":
        environment["ELECTRON_RUN_AS_NODE"] = "1"
        environment["VIGO_NATIVE_ROUTING_KERNEL"] = str(
            executable.parent.parent / "Resources" / "app" / "server" / "vigo-routing-kernel.node"
        )
    return environment


def _path_command(value: str | os.PathLike[str]) -> tuple[str, ...] | None:
    path = Path(value).expanduser()
    if path.suffix == ".app" or path.is_dir() and path.name.endswith(".app"):
        return _app_command(path)
    if path.is_file():
        if path.suffix == ".mjs" and (node := shutil.which("node")):
            return (node, str(path.resolve()))
        return (str(path.resolve()),)
    return None


def _candidates(explicit: str | os.PathLike[str] | Sequence[str] | None):
    if explicit is not None:
        if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes, os.PathLike)):
            command = tuple(str(part) for part in explicit)
            if command:
                yield "explicit", command
            return
        command = _path_command(explicit)  # type: ignore[arg-type]
        if command:
            yield "explicit", command
        return

    configured = os.environ.get("VIGO_RUNTIME")
    if configured:
        command = _path_command(configured)
        if command:
            yield "environment", command

    configured_app = os.environ.get("VIGO_APP")
    if configured_app:
        command = _app_command(Path(configured_app))
        if command:
            yield "studio", command

    sibling = Path(__file__).resolve().parents[2] / "vigo" / "public" / "vigo.mjs"
    node = shutil.which("node")
    if sibling.is_file() and node:
        yield "checkout", (node, str(sibling))

    for name in ("VIGO Studio.app", "VIGO.app"):
        for app in (Path("/Applications") / name, Path.home() / "Applications" / name):
            command = _app_command(app)
            if command:
                yield "studio", command

    installed = shutil.which("vigo")
    if installed:
        yield "path", (installed,)


def _run(command: Sequence[str], *arguments: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*command, *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=_command_environment(command),
        )
    except subprocess.TimeoutExpired as error:
        raise VigoTimeoutError(f"VIGO did not respond within {timeout:g} seconds") from error


def _compatible_api(value: object) -> bool:
    try:
        return int(str(value).split(".", 1)[0]) == int(API_VERSION.split(".", 1)[0])
    except (TypeError, ValueError):
        return False


def resolve_runtime(
    runtime: RuntimeInfo | str | os.PathLike[str] | Sequence[str] | None = None,
    *,
    verify: bool = True,
) -> RuntimeInfo:
    """Find a VIGO runtime whose public API is compatible with this package."""

    if isinstance(runtime, RuntimeInfo):
        return runtime
    failures: list[str] = []
    for source, command in _candidates(runtime):
        if not verify:
            return RuntimeInfo("unknown", API_VERSION, source, command[-1], command, {})
        response = _run(command, "capabilities")
        try:
            capabilities = json.loads(response.stdout) if response.returncode == 0 else None
        except json.JSONDecodeError:
            capabilities = None
        if (
            not isinstance(capabilities, dict)
            or not _compatible_api(capabilities.get("apiVersion"))
            or capabilities.get("cityFormatVersion") != CITY_FORMAT_VERSION
            or capabilities.get("resultSchemaVersion") != RESULT_SCHEMA_VERSION
        ):
            failures.append(" ".join(command))
            continue
        commands = capabilities.get("publicCliCommands")
        if not isinstance(commands, list) or any(name not in commands for name in PUBLIC_COMMANDS):
            failures.append(" ".join(command))
            continue
        return RuntimeInfo(
            product_version=str(capabilities.get("productVersion", "unknown")),
            api_version=str(capabilities["apiVersion"]),
            source=source,
            path=command[-1],
            command=command,
            capabilities=capabilities,
        )

    detail = f" Checked: {', '.join(failures)}." if failures else ""
    raise VigoError(
        f"No VIGO runtime compatible with API {API_VERSION} is available. "
        "Install VIGO Studio or set VIGO_RUNTIME."
        + detail
    )


def run_json(
    runtime: RuntimeInfo,
    arguments: Sequence[str],
    *,
    timeout: float,
) -> str:
    completed = _run(runtime.command, *arguments, timeout=timeout)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise VigoError(message)
    return completed.stdout
