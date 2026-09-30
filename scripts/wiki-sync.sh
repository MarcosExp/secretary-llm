#!/bin/bash
# Backs up the wiki to its private git remote. Run it from cron or a systemd timer
# (see deploy/systemd/ and docs/OPERATIONS.md), as the user that owns the wiki.
#
# 1. Commits edits made by hand that the agent did not commit.
# 2. Pulls edits made on the remote (e.g. in GitHub's web editor) and replays local
#    commits on top. If both sides changed the same lines it stops without touching
#    anything and exits with an error, so it can be resolved by hand.
# 3. Pushes.
# 4. Optionally pings a URL on success (e.g. an Uptime Kuma push monitor), so a sync
#    that stops working does not go unnoticed.
#
# Settings (environment):
#   WIKI_DIR            the wiki folder (default: $SECRETARY_DATA_DIR/wiki)
#   WIKI_REMOTE         git remote to sync with (default: origin)
#   WIKI_SYNC_PING_FILE file holding the URL to ping on success (optional)

set -euo pipefail

WIKI_DIR="${WIKI_DIR:-${SECRETARY_DATA_DIR:?set WIKI_DIR or SECRETARY_DATA_DIR}/wiki}"
REMOTE="${WIKI_REMOTE:-origin}"

log() { echo "[$(date -Is)] $*"; }
git_() { git -C "$WIKI_DIR" -c user.name=wiki-sync -c user.email=wiki-sync@localhost "$@"; }

BRANCH="$(git_ branch --show-current)"

if [[ -n "$(git_ status --porcelain)" ]]; then
    git_ add -A
    git_ commit -q -m "Manual edits (committed by wiki-sync)"
    log "committed manual edits"
fi

if ! git_ pull -q --rebase "$REMOTE" "$BRANCH"; then
    git_ rebase --abort 2>/dev/null || true
    log "ERROR: the wiki and $REMOTE changed the same lines; resolve it by hand in $WIKI_DIR"
    exit 1
fi

git_ push -q "$REMOTE" "$BRANCH"
log "pushed: $(git_ log -1 --format='%h %s')"

if [[ -n "${WIKI_SYNC_PING_FILE:-}" && -r "$WIKI_SYNC_PING_FILE" ]]; then
    curl -fsS -m 10 -o /dev/null "$(cat "$WIKI_SYNC_PING_FILE")" || log "WARNING: could not ping the monitor"
fi
