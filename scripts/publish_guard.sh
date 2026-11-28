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
if [ "$#" -eq 0 ]; then
  echo "usage: SUBJECT_SLUG=subject-0001 ./scripts/publish_rendered_subject.sh subject-0001" >&2
  exit 64
fi
# The old arbitrary command mode allowed a green receipt to precede a copy
# that reopened different bytes. Only the publisher that hashes and copies
# one open snapshot is an admitted effect. /usr/bin/true is a dry-run check.
if ! { [ "$#" -eq 4 ] && [ "$1" = "$TRUSTED_PYTHON" ] && [ "$2" = scripts/publish_verified_subject.py ] && [ "$3" = "$REPO" ] && [ "$4" = "$SUBJECT_SLUG" ]; } \
   && ! { [ "$#" -eq 1 ] && [ "$1" = /usr/bin/true ]; }; then
  echo "publish guard accepts only the verified subject publisher" >&2
  exit 64
fi
"$TRUSTED_PYTHON" scripts/check_export_prep.py "$REPO" "$SUBJECT_SLUG"
exec "$@"
