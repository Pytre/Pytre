#!/usr/bin/env bash
# Regenerates requirements-build.lock (pinned versions + hashes) used by the GitHub build workflow.
#   ./requirements-update_lock.sh                            resolve changes made to requirements*.txt, keep other versions
#   ./requirements-update_lock.sh --upgrade                  upgrade every dependency
#   ./requirements-update_lock.sh --upgrade-package fastapi  upgrade a single dependency
set -euo pipefail
cd "$(dirname "$0")"

uv pip compile requirements.txt requirements-server.txt \
    --universal \
    --python-version 3.14 \
    --generate-hashes \
    -o requirements-build.lock \
    "$@"
