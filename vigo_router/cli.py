"""Standalone console entry point that passes through to VIGO's canonical CLI."""

from __future__ import annotations

import subprocess
import sys

from .core import resolve_cli


def main(arguments: list[str] | None = None) -> int:
    """Run the current native/JavaScript CLI with an identical argument list."""

    command = resolve_cli()
    completed = subprocess.run(
        [*command, *(sys.argv[1:] if arguments is None else arguments)],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
