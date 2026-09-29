#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Run an investbell.paper command in a one-off container with the paper runner's
# image, data and keys, on the home server. See docs/paper-trading.md.
#
#   scripts/paper-container.sh init --config deploy/paper-policy.json
#   scripts/paper-container.sh status
#   scripts/paper-container.sh stop
#   scripts/paper-container.sh resume --reason 'Reviewed broker fills'
#   scripts/paper-container.sh evidence [--output /data/validation/NEW.json]
#
# The policy path is inside the image. Paths under /data are ~/investbell-data.
set -euo pipefail

SECRET=investbell-paper-env
OPTIONS=(--rm --pull never --userns keep-id:uid=10001,gid=10001 --read-only
         --cap-drop all --security-opt no-new-privileges -v "$HOME/investbell-data:/data")
# Only commands that contact the broker need keys; status works without the secret.
if podman secret exists "$SECRET"; then
    OPTIONS+=(--secret "$SECRET,type=mount,uid=10001,gid=10001,mode=0400")
fi
if [[ "${1:-}" == evidence ]]; then
    shift
    COMMAND=(python -m investbell.paper_evidence --ledger /data/paper.sqlite3 "$@")
else
    COMMAND=(python -m investbell.paper --ledger /data/paper.sqlite3 --env-file "/run/secrets/$SECRET" "$@")
fi
exec podman run "${OPTIONS[@]}" localhost/investbell:paper "${COMMAND[@]}"
