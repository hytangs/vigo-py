#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
environment_root=${1:-"$repository_root/.venv"}
python_command=${PYTHON:-python3}
runtime_parent="$environment_root/share/vigo-router/runtime"
runtime_destination="$runtime_parent/0.3.0"

if ! command -v "$python_command" >/dev/null 2>&1; then
  echo "Python 3.10 or newer was not found: $python_command" >&2
  exit 1
fi

"$python_command" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else "Python 3.10 or newer is required")'
"$python_command" -m venv "$environment_root"
"$environment_root/bin/python" -m pip install \
  --disable-pip-version-check \
  --no-deps \
  "$repository_root"
"$environment_root/bin/vigo-router-download" \
  --destination "$runtime_destination"
VIGO_ROUTER_RUNTIME_DIR="$runtime_parent" "$environment_root/bin/vigo-router" --version
"$environment_root/bin/python" -c \
  'import vigo_router; assert vigo_router.__version__ == "0.3.0"; print(vigo_router.__version__)'

echo "VIGO and vigo-router are installed in $environment_root"
echo "Activate with: . $environment_root/bin/activate"
