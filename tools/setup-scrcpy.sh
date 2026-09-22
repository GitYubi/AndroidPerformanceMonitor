#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
target="$repo_root/backend/vendor/scrcpy/scrcpy-server-v3.3.4"
mkdir -p "$(dirname "$target")"
tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT
curl -fL 'https://github.com/Genymobile/scrcpy/releases/download/v3.3.4/scrcpy-server-v3.3.4' -o "$tmp_file"
python3 - "$tmp_file" "$target" <<'PY'
import hashlib,pathlib,sys
source=pathlib.Path(sys.argv[1]); data=source.read_bytes()
if hashlib.sha256(data).hexdigest()!='8588238c9a5a00aa542906b6ec7e6d5541d9ffb9b5d0f6e1bc0e365e2303079e':
    raise SystemExit('scrcpy checksum mismatch')
pathlib.Path(sys.argv[2]).write_bytes(data)
PY
