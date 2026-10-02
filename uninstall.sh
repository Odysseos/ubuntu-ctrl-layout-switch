#!/usr/bin/env bash
set -Eeuo pipefail
DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$DIR/install.sh" --uninstall
