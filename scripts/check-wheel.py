from __future__ import annotations

import subprocess
import sys
import tempfile
import venv
from pathlib import Path

wheel, = Path(sys.argv[1]).resolve().glob("vigo-0.3.1-*.whl")
with tempfile.TemporaryDirectory(prefix="vigo-wheel-") as directory:
    root = Path(directory)
    venv.EnvBuilder(with_pip=True).create(root / "environment")
    executable = root / "environment" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(executable), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], check=True)
    subprocess.run([
        str(executable), "-c",
        (
            "import importlib.metadata, vigo; "
            "assert importlib.metadata.version('vigo') == '0.3.1'; "
            "assert all(callable(getattr(vigo, name)) for name in ('open', 'build', 'compare', 'resolve_runtime')); "
            "print('Installed wheel import passed.')"
        ),
    ], cwd=root, check=True)
