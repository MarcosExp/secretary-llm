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

## Web app (PWA)

Open the app's address in the phone's browser:
- through Tailscale: `https://<server>.<tailnet>.ts.net:8443`;
- on the server itself: `http://localhost:8000`.

Then use **Add to Home screen** (Android) or **Share → Add to Home Screen** (iOS) to install it.

- **Text:** type and send. The conversation is kept on the server; **New chat** starts a fresh one.
- **Voice:** tap the microphone, speak, and tap it again. The recording is transcribed on the server with faster-whisper and deleted right after; only the text is kept. The first recording after a restart takes a few extra seconds while the model loads.
- **Confirmations:** proposals appear as cards with **Confirm / Discard**. They work exactly like `secretary confirm/reject`.

The microphone only works over HTTPS or on `localhost`, so use the Tailscale HTTPS address on the phone.

There is no login: access is limited to your tailnet. Requests that change data must carry an `X-Secretary: 1` header, which the app sends. Other websites cannot add that header without a CORS permission the server never gives, so a page you visit cannot act on the app through your browser.

**Speech model:** it is downloaded on first use into `secretary-data/models/` (~500 MB for `small`). If transcription is poor, try `WHISPER_LANGUAGE=es` (skips language detection) or `WHISPER_MODEL=medium`, which is slower and more accurate.

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

## Google Calendar

The agent uses a Google **service account**, a technical account that you share your calendar with. It needs no browser on the server, its access never expires, and it can only reach the calendars you share with it.

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project (any name).
2. **APIs & Services → Library:** enable **Google Calendar API**.
3. **IAM & Admin → Service Accounts:** create a service account. It needs no roles.
4. Open the service account, go to **Keys → Add key → JSON**, and save the file as `../secretary-data/secrets/google-service-account.json`.
   - On Linux, also run `chmod 600` on it and `chown 1000:1000` so the app can read it.
   - The key only gives access to what you share in the next step. Treat it like a password anyway.
5. In **Google Calendar (web) → Settings → your calendar → Share with specific people**, add the service account's email (`…@….iam.gserviceaccount.com`) with **Make changes to events**.
6. In the same page, under **Integrate calendar**, copy the **Calendar ID**. For your main calendar, it is your account's email. Set it in `.env`:
   ```
   GOOGLE_CALENDAR_ID=<calendar id>
   ```
7. Run `docker compose up -d app`, then:
   ```sh
   docker compose exec app secretary ask "what do I have on the calendar this week?"
   ```

### Confirmations

Changes that need your approval are not run by the agent. They are stored and shown with an ID:
- moving events;
- creating several events at once.

```
Needs your confirmation (#4):
  Move 'Study block' (Thu 2026-10-01 17:00-18:00) to Fri 2026-10-02 18:00-19:00
  -> secretary confirm 4   |   secretary reject 4
```

- `secretary confirm 4` runs the stored call exactly as shown, without the model.
- `secretary reject 4` discards it.
- In `secretary chat`, you are asked right away.
- `secretary pending` lists open proposals. They expire after 24 hours.

The agent never deletes calendar events, never moves events that have other attendees, and never sends invitations. Events it creates carry a private marker, so later features can tell them apart from yours.

## Wiki (long-term memory)

The wiki is a folder of Markdown pages in `secretary-data/wiki/`, kept in its own local git repository. Create it once:

```sh
docker compose run --rm --no-deps app secretary wiki init
```

Then write your pages, by hand or by asking the agent:
- `profile.md`: who you are, goals and priorities;
- `rules.md`: how your time should be organized;
- `decisions.md`: a dated log of decisions.

How the agent uses them:
- The orchestrator reads `index.md`, `profile.md` and `rules.md` on every request.
- Every module subagent also gets `rules.md`, so the module that acts can flag conflicts.
- Every change the agent makes is a git commit (author `secretary`), so `git log` / `git revert` in that folder undo anything.
- The agent cannot change `rules.md` or `profile.md` without your confirmation, so a text it reads elsewhere cannot rewrite your rules.

To back the wiki up, add a private git remote to that folder and push, or include it in your server backups. It is not replicated by Litestream.

## Importing from Notion

`secretary import notion` copies courses, study units, job applications and tasks from Notion databases, including the links between them (task → course, task → application).

1. **Integration:** at [notion.so/profile/integrations](https://www.notion.so/profile/integrations), create an **internal** integration with **read content** capability only, and copy its secret into `.env`:
   ```
   NOTION_TOKEN=<secret>
   ```
2. **Access:** in Notion, open the page that contains your databases, then **⋯ → Connections → add the integration**. Pages below it are shared too.
3. **Mapping:** copy `examples/notion.example.yaml` to `../secretary-data/config/notion.yaml` and fill in:
   - each database id (the 32 characters in its URL);
   - your property names;
   - how your select options map to Secretary's values.

   A mapping like `"Blocked": "todo | Blocked"` sets the status to `todo` and keeps "Blocked" in the notes.
4. **Preview, then import:**
   ```sh
   docker compose run --rm --no-deps app secretary import notion --dry-run
   docker compose run --rm --no-deps app secretary import notion
   ```

What happens:
- The import runs in one transaction: all or nothing.
- Every row remembers its Notion page (`external_ref`). Running the import again only adds pages that are new since last time and never overwrites anything, so you can keep using Notion during the transition.
- Values with no mapping get a default and a warning, and the original value is kept in the notes.
- Notion is only read, never modified.

## Data view and token log

- **Data** (`/dashboard`, linked from the app header) shows every table with its row count and a count per status, and the rows of the table you pick, newest first. It is read-only; long values are cut.
- **Tokens CSV** (`/api/logs.csv`) downloads `core_agent_log`: one row per request with the time (UTC), models, input and output tokens, tool calls and your message. It opens directly in Excel or Sheets.

## Archived rows

Anything archived (tasks, areas, schedule blocks, courses, units, programs, applications) is deleted after 30 days, unless you reopen it first; archiving it again restarts the count. The app checks once a day and at startup. To run it by hand:

```sh
docker compose exec app secretary db purge
```

Areas and programs are kept while something still uses them; a course takes its units with it. Deleted rows stay recoverable through the backups' point-in-time history (30 days).

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
