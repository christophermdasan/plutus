#!/usr/bin/env bash
# Plutus - double-click to install anything missing and start the app.
# Finder opens .command files in the directory the Terminal last used, not
# the file's own, so the path is resolved from the script location.
cd "$(dirname "$0")" || exit 1
./scripts/bootstrap.sh "$@"
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "Setup did not finish. The error above says why."
  read -r -p "Press Enter to close…"
fi
