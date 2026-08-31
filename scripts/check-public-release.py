#!/usr/bin/env python3
"""Reject files and references that do not belong in the public binding."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {
    ".venv",
    "build",
    "dist",
    "paper",
    "release",
    "runtime",
    "wheelhouse",
}
FORBIDDEN_SUFFIXES = {".env", ".pdf", ".pbf", ".sqlite", ".whl", ".zip"}
FORBIDDEN_NAMES = {"benchmark-vigo-router.py"}
TEXT_SUFFIXES = {".md", ".py", ".sh", ".toml", ".yaml", ".yml"}
TEXT_PATTERNS = {
    "developer home path": re.compile(r"/(?:Users|home)/[^/\s]+/"),
    "Windows developer home path": re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\s]+\\\\"),
    "private development remote": re.compile(r"github\.com/hytangs/vigo-dev"),
    "GitHub token": re.compile(r"\bgh[opsu]_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in completed.stdout.split(b"\0") if item]


def main() -> None:
    files = tracked_files()
    violations: list[str] = []
    scanned = 0
    for path in files:
        relative = path.relative_to(ROOT)
        if path.name in FORBIDDEN_NAMES:
            violations.append(f"excluded experiment script: {relative}")
        if any(part in FORBIDDEN_PARTS for part in relative.parts[:-1]):
            violations.append(f"excluded directory: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"excluded artifact: {relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8")
        for label, pattern in TEXT_PATTERNS.items():
            if pattern.search(text):
                violations.append(f"{label}: {relative}")

    version = "0.3.0"
    if f'version = "{version}"' not in (ROOT / "pyproject.toml").read_text():
        violations.append(f"pyproject version does not match {version}")
    if f'__version__ = "{version}"' not in (ROOT / "vigo_router/__init__.py").read_text():
        violations.append(f"package version does not match {version}")
    if violations:
        raise SystemExit("\n".join(violations))
    print(
        json.dumps(
            {
                "status": "passed",
                "version": version,
                "trackedFiles": len(files),
                "scannedTextFiles": scanned,
                "packagedRuntimePresent": False,
                "publicationMaterialPresent": False,
                "experimentScriptsPresent": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
