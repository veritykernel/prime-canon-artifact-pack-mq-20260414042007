#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONDONTWRITEBYTECODE=1
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
TRUSTED_PYTHON="${PRIME_CANON_TRUSTED_PYTHON:-}"
if [ -z "$TRUSTED_PYTHON" ]; then
  echo "PRIME_CANON_TRUSTED_PYTHON must name an operator-controlled interpreter outside this repository" >&2
  exit 64
fi
/usr/bin/python3 scripts/verify_trusted_python.py "$REPO" "$TRUSTED_PYTHON"
SUBJECT_SLUG="${SUBJECT_SLUG:-subject-0001}"
if [ "${1:-}" = "--" ]; then
  shift
fi
if ! { [ "$#" -eq 1 ] && { [ "$1" = publish ] || [ "$1" = /usr/bin/true ]; }; }; then
  echo "publish guard accepts only publish or the no-effect /usr/bin/true check" >&2
  exit 64
fi
# -I removes the candidate script directory from interpreter startup. Import
# dependencies before appending frozen repository modules after site-packages.
"$TRUSTED_PYTHON" -I -c '
import sys
import yaml, jsonschema
sys.path.append(sys.argv[1] + "/scripts")
from check_export_prep import main
main()
' "$REPO" "$SUBJECT_SLUG"
if [ "$1" = /usr/bin/true ]; then
  exit 0
fi
exec "$TRUSTED_PYTHON" -I -c '
import sys
from pathlib import Path
import yaml, jsonschema
sys.path.append(sys.argv[1] + "/scripts")
from publish_verified_subject import publish
destination = publish(Path(sys.argv[1]), sys.argv[2])
print(f"published_to={destination}")
' "$REPO" "$SUBJECT_SLUG"
