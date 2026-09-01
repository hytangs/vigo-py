"""Install and verify the packaged VIGO native/JavaScript runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

_PACKAGE_VERSION = "0.3.0"
_ARCHIVE_NAME = "VIGO-{version}-mac-arm64.zip"
_OFFICIAL_RUNTIME_RELEASES = {
    "0.3.0": {
        "url": "https://github.com/hytangs/vigo/releases/download/v0.3.0/VIGO-0.3.0-mac-arm64.zip",
        "sha256": "8ca5a9f2cc4e8fcfb28eaae7686d1697869958596104bc1c8bb51efee44b49a3",
    }
}
_REQUIRED_HELP_MARKERS = (
    "vigo build-network",
    "vigo route-ndjson",
    "vigo one-to-many",
    "vigo isochrone",
)


@dataclass(frozen=True, slots=True)
class RuntimeInstall:
    """Result of a verified VIGO runtime installation."""

    version: str
    root: Path
    launcher: Path
    archive_sha256: str | None
    source: str | None
    reused: bool

    def to_dict(self) -> dict[str, str | bool | None]:
        payload = asdict(self)
        payload["root"] = str(self.root)
        payload["launcher"] = str(self.launcher)
        return payload


@dataclass(frozen=True, slots=True)
class CliProbe:
    """Verified identity and command surface for one VIGO CLI."""

    command: tuple[str, ...]
    version: str
    required_commands: tuple[str, ...]


def default_runtime_directory(version: str = _PACKAGE_VERSION) -> Path:
    """Return the per-user installation directory for one VIGO version."""

    configured = os.environ.get("VIGO_ROUTER_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser().resolve() / version
    return Path.home() / ".local" / "share" / "vigo-router" / "runtime" / version


def installed_runtime_candidates(version: str = _PACKAGE_VERSION) -> tuple[Path, ...]:
    target = default_runtime_directory(version)
    if _installed_runtime(target, version) is None:
        return ()
    root = target / "VIGO-mac-arm64"
    return root / "vigo", root / "VIGO.app"


def install_runtime(
    *,
    archive: str | os.PathLike[str] | None = None,
    url: str | None = None,
    sha256: str | None = None,
    destination: str | os.PathLike[str] | None = None,
    version: str = _PACKAGE_VERSION,
    force: bool = False,
) -> RuntimeInstall:
    """Download or install a checksum-pinned packaged VIGO environment.

    With no source argument, the installer uses the official versioned runtime
    URL and digest embedded in this package. Custom URLs require an explicit
    digest through ``sha256`` or ``VIGO_RUNTIME_SHA256``.
    """

    target = _safe_destination(
        Path(destination).expanduser().resolve()
        if destination is not None
        else default_runtime_directory(version)
    )
    current = _installed_runtime(target, version)
    if current is not None and not force:
        return RuntimeInstall(version, target, current, None, None, True)
    _require_replaceable_destination(target, current)

    expected_sha256 = (
        str(sha256 or os.environ.get("VIGO_RUNTIME_SHA256") or "").strip().lower()
    )
    source_url = str(url or os.environ.get("VIGO_RUNTIME_URL") or "").strip() or None
    archive_path = Path(archive).expanduser().resolve() if archive is not None else None
    if archive_path is None and source_url is None:
        archive_path = _local_release_archive(version)
    if archive_path is None and source_url is None:
        official = _OFFICIAL_RUNTIME_RELEASES.get(version)
        if official is not None:
            _require_official_runtime_platform(version)
            source_url = official["url"]
            if not expected_sha256:
                expected_sha256 = official["sha256"]
    if archive_path is None and source_url is None:
        raise ValueError(
            "no VIGO runtime source is configured; pass --archive or --url with --sha256"
        )

    if archive_path is not None and not archive_path.is_file():
        raise FileNotFoundError(f"VIGO runtime archive not found: {archive_path}")
    if not expected_sha256 and archive_path is not None:
        expected_sha256 = _sidecar_sha256(archive_path) or ""
    if (
        not expected_sha256
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("a lowercase 64-character runtime SHA-256 is required")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vigo-runtime-download-") as temporary:
        temporary_root = Path(temporary)
        if source_url is not None:
            archive_path = temporary_root / _ARCHIVE_NAME.format(version=version)
            _download(source_url, archive_path)
        assert archive_path is not None
        actual_sha256 = _sha256_file(archive_path)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"VIGO runtime SHA-256 mismatch: expected {expected_sha256}, received {actual_sha256}"
            )

        staging = Path(
            tempfile.mkdtemp(prefix=f".{target.name}-install-", dir=target.parent)
        )
        published = False
        try:
            _safe_extract(archive_path, staging)
            staged_launcher = staging / "VIGO-mac-arm64" / "vigo"
            _require_runtime_version(staged_launcher, version)
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
            published = True
        finally:
            if not published and staging.exists():
                shutil.rmtree(staging)

    launcher = target / "VIGO-mac-arm64" / "vigo"
    _require_runtime_version(launcher, version)
    return RuntimeInstall(
        version=version,
        root=target,
        launcher=launcher,
        archive_sha256=expected_sha256,
        source=source_url or str(archive_path),
        reused=False,
    )


def _safe_destination(path: Path) -> Path:
    if path == Path(path.anchor) or path == Path.home():
        raise ValueError(f"unsafe runtime destination: {path}")
    return path


def _require_replaceable_destination(target: Path, current: Path | None) -> None:
    """Refuse to remove a populated directory not owned by this installer."""

    if not target.exists() or current is not None:
        return
    if target.is_dir() and next(target.iterdir(), None) is None:
        return
    raise ValueError(f"refusing to replace non-VIGO runtime destination: {target}")


def _installed_runtime(target: Path, version: str) -> Path | None:
    launcher = target / "VIGO-mac-arm64" / "vigo"
    try:
        _require_runtime_version(launcher, version)
    except (FileNotFoundError, RuntimeError, OSError):
        return None
    return launcher


def _local_release_archive(version: str) -> Path | None:
    module_path = Path(__file__).resolve()
    archive_name = _ARCHIVE_NAME.format(version=version)
    candidates = (module_path.parents[1] / "release" / archive_name,)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _require_official_runtime_platform(version: str) -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError(
            f"the official VIGO {version} runtime requires Apple-silicon macOS; "
            "on another platform, install a compatible VIGO CLI and set VIGO_CLI"
        )


def _sidecar_sha256(archive: Path) -> str | None:
    sidecar = archive.with_name(f"{archive.name}.sha256")
    if not sidecar.is_file():
        return None
    first = sidecar.read_text(encoding="utf-8").split()
    return first[0].lower() if first else None


def _download(url: str, destination: Path) -> None:
    if urlsplit(url).scheme.lower() != "https":
        raise ValueError("VIGO runtime URL must use HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "vigo-router-runtime"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if urlsplit(response.geturl()).scheme.lower() != "https":
            raise ValueError("VIGO runtime redirect must use HTTPS")
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = PurePosixPath(member.filename)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(
                    f"runtime archive contains an unsafe path: {member.filename}"
                )
            if not name.parts or name.parts[0] == "__MACOSX":
                continue
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(
                    f"runtime archive contains an unsupported symlink: {member.filename}"
                )
            output = destination.joinpath(*name.parts)
            if member.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            permissions = mode & 0o777
            if permissions:
                output.chmod(permissions)


def probe_cli(
    command: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
    *,
    expected_version: str = _PACKAGE_VERSION,
    timeout: float = 30,
) -> CliProbe:
    """Require an exact VIGO version and the complete Python-facing CLI surface.

    Probe results are cached against the resolved executable/module file
    identities, so replacing a command at the same path invalidates the cache.
    """

    parts = (
        (os.fspath(command),)
        if isinstance(command, (str, os.PathLike))
        else tuple(os.fspath(part) for part in command)
    )
    if not parts:
        raise ValueError("VIGO CLI command cannot be empty")
    resolved: list[str] = []
    identity: list[tuple[int, int, int, int]] = []
    for index, part in enumerate(parts):
        path = Path(part).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"VIGO CLI command file is missing: {path}")
        if index == 0 and not os.access(path, os.X_OK):
            raise FileNotFoundError(f"VIGO CLI launcher is not executable: {path}")
        details = path.stat()
        resolved.append(str(path))
        identity.append(
            (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns)
        )
    if timeout <= 0:
        raise ValueError("VIGO CLI probe timeout must be positive")
    return _probe_cli_cached(
        tuple(resolved),
        tuple(identity),
        str(expected_version),
        float(timeout),
    )


@lru_cache(maxsize=32)
def _probe_cli_cached(
    command: tuple[str, ...],
    _identity: tuple[tuple[int, int, int, int], ...],
    expected_version: str,
    timeout: float,
) -> CliProbe:
    try:
        completed = subprocess.run(
            [*command, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"VIGO runtime version probe timed out after {timeout:g} seconds"
        ) from error
    reported = completed.stdout.strip()
    if completed.returncode != 0 or reported != expected_version:
        raise RuntimeError(
            "VIGO runtime reports "
            f"{reported or 'no version'}, expected {expected_version}"
        )
    try:
        help_result = subprocess.run(
            [*command, "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"VIGO runtime help probe timed out after {timeout:g} seconds"
        ) from error
    missing_commands = [
        marker for marker in _REQUIRED_HELP_MARKERS if marker not in help_result.stdout
    ]
    if help_result.returncode != 0 or missing_commands:
        raise RuntimeError(
            "VIGO runtime is missing required Python binding commands: "
            f"{', '.join(missing_commands) or 'help unavailable'}"
        )
    return CliProbe(command, reported, _REQUIRED_HELP_MARKERS)


def _require_runtime_version(launcher: Path, version: str) -> None:
    probe_cli(
        (str(launcher),),
        expected_version=version,
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install a checksum-verified VIGO native/JavaScript runtime."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--archive", help="Local VIGO standalone ZIP")
    source.add_argument("--url", help="HTTPS URL for a VIGO standalone ZIP")
    parser.add_argument("--sha256", help="Expected archive SHA-256")
    parser.add_argument("--destination", help="Installation directory")
    parser.add_argument("--version", default=_PACKAGE_VERSION)
    parser.add_argument("--force", action="store_true")
    options = parser.parse_args(arguments)
    installed = install_runtime(
        archive=options.archive,
        url=options.url,
        sha256=options.sha256,
        destination=options.destination,
        version=options.version,
        force=options.force,
    )
    print(json.dumps(installed.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
