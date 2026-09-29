# Operations

How to deploy, back up and restore Secretary on a home server.

## Stack

| Service | Role |
|---|---|
| `app` | FastAPI app, published on the host's loopback only (`127.0.0.1:${APP_PORT}`). |
| `litestream` | Continuously replicates `secretary.db` to an S3-compatible bucket. |
| `tailscale` | *Optional* (`compose.tailscale.yml`). Only for hosts without Tailscale installed. |

`app` and `litestream` run as UID 1000 with a read-only root filesystem, no capabilities and `no-new-privileges`. They only mount `SECRETARY_DATA_DIR/db`.

The app is never exposed to the internet. It is reached through Tailscale in one of two ways:

- **Host Tailscale (recommended if the server already runs Tailscale).** The host's `tailscale serve` proxies HTTPS to the loopback port. The app is served under the server's existing tailnet name.
- **Sidecar.** A Tailscale container joins the tailnet as its own node (`secretary`). The app shares its network and nothing is published on the host.

Both give an HTTPS URL, which the PWA needs for its service worker and the microphone.

## First deployment

1. **Bucket.** Create a private bucket in Cloudflare R2 (or Backblaze B2) and an API token scoped to that bucket only, with read and write access.
2. **Config.**
   ```sh
   cp .env.example .env      # fill in LITESTREAM_*
   mkdir -p ../secretary-data/db
   ```
   On Linux, make `db/` writable by UID 1000: `sudo chown -R 1000:1000 ../secretary-data/db`.
3. **Start.**
   ```sh
   docker compose up -d --build
   docker compose logs litestream   # expect "snapshot written"
   curl http://127.0.0.1:8000/health
   ```
4. **Expose on the tailnet.** Pick the option that matches your server.

### Option A: host Tailscale

HTTPS certificates must be enabled in the Tailscale admin console. Check which ports are already served, then pick a free HTTPS port (443 if it is unused):

```sh
tailscale serve status
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8000
```

The app is now at `https://<server>.<tailnet>.ts.net:8443`. The setting persists across reboots. To remove it:

```sh
sudo tailscale serve --https=8443 off
```

### Option B: sidecar

Enable MagicDNS and HTTPS certificates, create a reusable, pre-approved auth key and set `TS_AUTHKEY` in `.env`. Then:

```sh
mkdir -p ../secretary-data/tailscale
docker compose -f docker-compose.yml -f compose.tailscale.yml up -d --build
```

The app is at `https://secretary.<tailnet>.ts.net`.

## Command-line interface

Run the CLI inside the `app` container so it uses the same database file as the running app:

```sh
docker compose exec app secretary task add "Submit assignment" -p P1 -a Studies -d 2027-01-15 -e 3
docker compose exec app secretary task list
docker compose exec app secretary --help
```

### Talking to the agent

**With a Claude subscription (Pro/Max):**

1. On any machine with a browser and Claude Code installed, run `claude setup-token`. It prints a one-year token and does not save it anywhere.
2. Put it in the server's `.env` and select the provider:
   ```
   LLM_PROVIDER=subscription
   CLAUDE_CODE_OAUTH_TOKEN=<token>
   ```
3. Recreate the container: `docker compose up -d --build app`.

Agent usage shares the plan's limits with your chat and Claude Code use. Do not set `ANTHROPIC_API_KEY` at the same time: it takes precedence over the token.

**With an API key:** set `LLM_PROVIDER=api` and `ANTHROPIC_API_KEY` instead, and set a monthly spend limit in the Anthropic Console.

```sh
docker compose exec app secretary ask "add submit the lab report next Friday, about 3 hours" -v
docker compose exec -it app secretary chat
```

`-v` lists the tool calls. Every reply ends with the number of tool calls, the tokens used and the models involved. The same data is stored in `core_agent_log`.

Avoid opening the live database from the host while the stack runs. On Docker Desktop (Windows/macOS), SQLite locks are not reliable across the bind mount.

To try the project with fictional data, point `SECRETARY_DATA_DIR` at an empty directory and run `secretary db seed-example`. It refuses to run on a database that already has tasks.

## Backups

- **What:** the SQLite database, replicated continuously, with 30 days of point-in-time history and a daily snapshot.
- **Encryption:** at rest by the bucket provider. The bucket is private and the token is scoped to it.
- **Not covered here:**
  - the wiki, backed up by its own private git remote;
  - `secrets/`, which you should keep in your password manager.

## Monthly restore test

This test proves the replica is usable:

1. It writes a probe row and waits for replication.
2. It restores the database from the bucket into a scratch file.
3. It checks the restored file's integrity, the probe row, and that every live table is present.

```sh
scripts/restore-test.sh
# with the sidecar: COMPOSE="docker compose -f docker-compose.yml -f compose.tailscale.yml" scripts/restore-test.sh
```

## Disaster recovery (new machine)

```sh
git clone <repo> && cd secretary-llm
cp <saved .env> .env
mkdir -p ../secretary-data/db
docker compose run --rm --no-deps litestream restore /data/secretary.db
docker compose up -d --build
```

Then expose the app on the tailnet again (step 4). To recover the database as it was at a given moment, add `-timestamp 2027-01-15T10:00:00Z` to the restore command.
