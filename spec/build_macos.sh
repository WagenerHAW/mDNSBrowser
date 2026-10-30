#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
cd "$project_root"

if [ ! -x .venv/bin/python ]; then
  echo "Project virtual environment not found: $project_root/.venv" >&2
  exit 1
fi

exec .venv/bin/python -m PyInstaller \
  --clean \
  --noconfirm \
  --windowed \
  --osx-bundle-identifier='mDNSBrowser' \
  --paths='src' \
  --add-data='assets:assets' \
  --hidden-import='zeroconf._utils.ipaddress' \
  --hidden-import='zeroconf._handlers.answers' \
  --hidden-import='async_upnp_client.search' \
  --hidden-import='async_upnp_client.ssdp' \
  --hidden-import='upnp' \
  --icon='assets/default_icon.icns' \
  --name='mDNSBrowser' \
  src/app.py
