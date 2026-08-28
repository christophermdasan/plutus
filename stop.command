#!/usr/bin/env bash
# Plutus - stop the app and its data services. Your data is kept.
cd "$(dirname "$0")" || exit 1
./scripts/bootstrap.sh --stop
read -r -p "Press Enter to close…"
