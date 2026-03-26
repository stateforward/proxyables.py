#!/bin/zsh
set -euo pipefail
.venv/bin/python parity/agent.py "$@"
