#!/usr/bin/env bash
set -euo pipefail
nonce="$1"
[[ $nonce =~ ^[0-9a-f]{32}$ ]] || exit 2
printf '{"items":2,"status":"ok","marker":"REPLACE_WITH_FIXTURE_MARKER","nonce":"%s"}\n' "$nonce" > result.json
