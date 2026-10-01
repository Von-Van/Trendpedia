#!/bin/bash
# Double-click this file to set up Trendpedia and add it to your Applications.
cd "$(dirname "$0")" || exit 1

PYTHON="$(command -v python3)"
if [ -z "$PYTHON" ]; then
  osascript -e 'display dialog "Python 3 is required but was not found.

Install it from python.org (or run “xcode-select --install” in Terminal), then double-click this file again." with title "Trendpedia" buttons {"OK"} default button "OK" with icon caution' >/dev/null 2>&1
  exit 1
fi

"$PYTHON" scripts/install_desktop.py
STATUS=$?
echo
if [ $STATUS -eq 0 ]; then
  echo "Setup complete. You can close this window."
else
  echo "Setup did not finish. The messages above explain why."
fi
echo
read -n 1 -s -r -p "Press any key to close…"
echo
