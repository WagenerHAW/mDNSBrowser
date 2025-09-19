#!/usr/bin/env bash
if [ -f src/app.py ]; then
  if [ -d .venv ]; then
    source ./.venv/bin/activate
    pyinstaller \
      --windowed \
      --hide-console="hide-early" \
      --osx-bundle-identifier='mDNSBrowser' \
      --add-data='assets:assets' \
      --add-data='src/os_qt_tools.py:.' \
      --hidden-import='zeroconf._utils.ipaddress' \
      --hidden-import='zeroconf._handlers.answers' \
      --icon='assets/default_icon.icns' \
      --name='mDNSBrowser' \
      src/app.py
  else
    echo "venv not found"
  fi
else
  echo "app.py not found"
fi
