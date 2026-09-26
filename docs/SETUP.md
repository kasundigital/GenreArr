# GenreArr Setup Guide

This guide is written for users who are new to Docker, Radarr, Sonarr, and APIs. You do not need to be a developer.

## What GenreArr does

GenreArr organizes media **after Radarr or Sonarr has imported it**. Example:

```text
Movie downloaded → Radarr imports it → GenreArr checks its genres
                                      ↓
                              Horror detected
                                      ↓
                         /movies/Horror/Movie Name
```

GenreArr asks Radarr/Sonarr to perform the move. Do not manually move the files yourself.

## Before you start

You need:

- A server or computer already running Radarr and/or Sonarr.
- Docker and Docker Compose.
- The IP address or hostname of your server.
- Your Radarr/Sonarr API key.
- Existing Radarr/Sonarr root folders for the destinations you want to use.

> Start with a test library if possible. Keep **Dry Run** enabled until the Planner shows the paths you expect.

## 1. Install GenreArr — one command

You do **not** need Git and you do not need to clone the repository.

Make sure Docker and Docker Compose are installed, then paste this single command into your server terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo bash
```

The installer automatically:

1. Downloads the latest GenreArr source.
2. Installs it under `/opt/genrearr`.
3. Creates the persistent data directory.
4. Generates a random security secret.
5. Generates an admin password.
6. Builds and starts GenreArr with Docker.
7. Shows the URL and password when finished.

You should see something similar to:

```text
✅ GenreArr is running
🌐 Open: http://192.168.1.50:3033
🔐 Admin password: YOUR-GENERATED-PASSWORD
📁 Data: /opt/genrearr/data
```

Save the generated password somewhere safe.

Open the displayed address in your browser and log in.

### Updating GenreArr

Run the same installer command again:

```bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo bash
```

Your existing configuration/database under `/opt/genrearr/data` and your generated `.env` credentials are retained.

### Custom port

If port 3033 is already used:

```bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo GENREARR_PORT=3034 bash
```

Then open port `3034` instead.

## 2. Find your Radarr API key

In Radarr:

1. Open **Settings**.
2. Open **General**.
3. Find **Security**.
4. Copy the **API Key**.

For Sonarr, use the same process: **Settings → General → Security → API Key**.

Treat API keys like passwords. Do not post them in screenshots or GitHub issues.

## 3. Add Radarr to GenreArr

Open **Instances** in GenreArr.

Enter:

| Field | Example | Meaning |
|---|---|---|
| Name | My Radarr | Any friendly name |
| Type | Radarr | Select Radarr |
| URL | `http://192.168.1.50:7878` | Address GenreArr can use to reach Radarr |
| API Key | your key | Key copied from Radarr |

Press **Add instance**, then **Test + roots**.

Use **Edit** to change the name, URL or API key later (leave the API key empty to keep the current one). Editing keeps the webhook URL, so Radarr/Sonarr need no changes. **Disable** pauses an instance without deleting it. **New webhook URL** replaces the secret in the webhook address, for example if it leaked; update it in Radarr/Sonarr afterwards.

A successful connection should report the root folders Radarr knows.

### If Radarr and GenreArr are in Docker

If they share the same Docker network, you may be able to use a container/service address such as:

```text
http://radarr:7878
```

If they are not on the same Docker network, use an address that is reachable **from inside the GenreArr container**, such as the server LAN IP.

## 4. Prepare destination root folders

GenreArr should route only to paths that Radarr/Sonarr recognizes as root folders.

Example Radarr roots:

```text
/movies
/movies/Kids
/movies/Horror
/movies/Documentary
/movies/Animation
```

Example Sonarr roots:

```text
/tv
/tv/Kids
/tv/Anime
/tv/Documentary
```

The exact paths depend on your Docker mappings. **Use the paths shown by Radarr/Sonarr, not a guessed host path.**

For example, your host might use `/mnt/media/movies`, while Radarr sees it as `/movies`. GenreArr should use the Radarr-visible path.

## 5. Create your first rule

Open **Smart Rules**.

A simple Horror rule:

```text
Media: Movie
Genre: Horror
Match: ALL
Target: /movies/Horror
Priority: 20
```

A Kids rule using two genres:

```text
Media: Movie
Genre: Animation + Family
Match: ALL
Target: /movies/Kids
Priority: 10
```

Because priority **10** is evaluated before **20**, the more specific Kids rule can win first.

### Several Radarr or Sonarr instances

By default a rule applies to **all** instances of its type. If you run, for example, one Radarr for HD and one for 4K with different root folders, choose **Only <instance>** in the rule's **Instance** field. The target is then checked against that instance's root folders only. Deleting an instance disables the rules limited to it.

### ALL vs ANY

**ALL** means every genre you enter must exist.

```text
Animation + Family
```

matches a movie containing both Animation and Family.

**ANY** means at least one entered genre must exist.

## 6. Smart conditions

Rules can also include optional conditions.

Examples:

```text
Animation + language ja → /movies/Anime
Documentary             → /movies/Documentary
Year before 1980        → /movies/Classics
Tag ID 4                → /movies/4K
```

Leave conditions blank when you do not need them.

**Language** accepts a two-letter code (`ja`, `ko`, `si`) or the name Radarr/Sonarr shows (`Japanese`). Separate several with commas: `ja, ko`.

GenreArr checks the **Target** when you save a rule. If Radarr/Sonarr does not list it as a root folder, the rule is rejected and the known roots are shown, so add the folder in **Radarr/Sonarr → Settings → Media Management → Root Folders** first.

## 7. Use Library Planner before moving anything

Open **Planner**.

GenreArr displays:

- Title
- Genres
- Current path
- Proposed destination
- Matching decision
- Safety checks

Example:

```text
The Conjuring (2013)
Genres: Horror, Thriller

Current:
 /movies/The Conjuring (2013)

Proposed:
 /movies/Horror/The Conjuring (2013)

Decision:
 Rule #20

Safety:
 ✓ Destination is an Arr root
 ✓ Enough free space
 ✓ Not active in queue
 ✓ No destination collision
```

Do not perform a live move if a safety check fails.

### Optional: detect folders already on disk

GenreArr normally only knows what Radarr/Sonarr know. If a folder with the same name already exists at the destination but is not in the library, the move could merge into it. To let GenreArr check the disk too, give it read-only access to your media and tell it how paths map.

1. Create `/opt/genrearr/docker-compose.override.yml` (the installer never overwrites this file):

   ```yaml
   services:
     genrearr:
       volumes:
         - /mnt/media/movies:/media/movies:ro
         - /mnt/media/tv:/media/tv:ro
   ```

2. Run `cd /opt/genrearr && docker compose up -d`.
3. In **Settings → Path mappings**, map each Radarr/Sonarr path to the path inside GenreArr, one per line:

   ```text
   /movies=/media/movies
   /tv=/media/tv
   ```

The Planner then shows **Destination folder not already on disk**. If a mapped root is missing inside the container, the check fails with **Destination root visible on disk**, so a broken mount never passes silently.

## 8. Test with Dry Run

Dry Run does **not intentionally perform the real move**. It records what GenreArr plans to do.

Select one or more titles in Planner and choose **Preview selected**.

Check the proposed destinations carefully.

## 9. Test one real movie

After Dry Run looks correct:

1. Pick one small, non-critical test movie.
2. Select it in Planner.
3. Choose **Move selected**.
4. Wait for Radarr to complete the operation.
5. Check the movie path in Radarr.
6. Check that the actual file/folder is in the expected location.
7. Play the movie to confirm it is still accessible.

The history entry says whether Radarr/Sonarr **confirmed the files moved**. If Radarr reports the file move failed (for example a full disk or a permissions problem), GenreArr records an error and does not retry. Check **Radarr → System → Tasks / Logs**.

**Undo** is available on the most recent move of each title. It moves the title back and records an `undo` entry.

Do not bulk-move your whole library until this works correctly.

## 10. Add Sonarr

Add Sonarr from **Instances** in the same way.

For Sonarr, GenreArr routes the **series folder**. It does not place individual episodes into genre folders independently.

Example:

```text
/tv/Animation/Series Name/
    Season 01/
    Season 02/
```

Test with one series before enabling larger moves.

## 11. Configure automatic sorting with Webhook

GenreArr shows a unique webhook path beside each instance.

It looks similar to:

```text
/webhook/1/UNIQUE_SECRET
```

Combine that with the address Radarr/Sonarr can use to reach GenreArr:

```text
http://GENREARR-SERVER:3033/webhook/1/UNIQUE_SECRET
```

In Radarr or Sonarr:

1. Open **Settings**.
2. Open **Connect**.
3. Add a **Webhook** connection.
4. Paste the full GenreArr webhook URL.
5. Enable download/import-related events.
6. Save and test the connection.

After a completed import, GenreArr evaluates that title automatically. It waits about 20 seconds first so Radarr/Sonarr can finish the import (set `WEBHOOK_DELAY` in seconds to change this). Moves only happen when Dry Run is off.

## 12. Health page

Open **Health** to check:

- Arr API connection
- Root-folder discovery
- Free space
- Last webhook received

If **Last webhook** always says `Never`, check whether Radarr/Sonarr can reach the GenreArr URL.

## 13. Scheduled reconciliation

Webhooks handle new imports. Scheduled reconciliation can periodically scan the library for anything that was missed.

Open **Settings**, enable scheduled reconciliation, and choose an interval.

Keep this disabled until your rules and live moves have been tested.

**History retention** (Settings) removes preview, skip and error entries older than the chosen number of days (default 30; `0` keeps everything). Moves and undos are always kept. Repeated scans with the same result update one entry instead of adding new ones.

Jobs are stored in the database. If GenreArr restarts during a job, the job shows as `interrupted`; run the scan again to continue.

### Notifications

Fill in the Discord webhook and/or Telegram bot token and chat ID in **Settings**, save, then press **Send test notification** to check that they work.

## 14. Backup your GenreArr settings

Open **Settings → Backup / Restore**.

Use **Export JSON** before making large changes. API keys and webhook secrets are not included, but the export can contain Discord/Telegram notification tokens, so keep it private.

Use **Restore JSON** to restore rules/settings/exclusions from a compatible GenreArr backup. Rules limited to an instance that does not exist on this install are imported disabled; re-enable them after choosing an instance.

## Troubleshooting

### GenreArr cannot connect to Radarr/Sonarr

Check:

- URL and port are correct.
- API key is correct.
- Radarr/Sonarr is running.
- The address is reachable from the GenreArr container.
- Docker networks/firewall are not blocking communication.

Useful command:

```bash
docker logs --tail 100 genrearr
```

### Root folder is rejected

Use **Smart Rules → Root folder discovery**. Copy the exact path returned by Radarr/Sonarr.

### A title is skipped

Open Planner and read the decision/safety information. Common reasons include:

- No matching rule
- No fallback
- Download/import still active
- Destination is not a recognized root
- Not enough free space
- Destination collision
- Exclusion rule matched

### Webhook does not work

Radarr/Sonarr must be able to reach GenreArr. `localhost:3033` usually means the Radarr/Sonarr machine or container itself, not GenreArr.

Use a reachable GenreArr IP/hostname/container name instead.

### Permission denied when saving data

GenreArr runs as user/group `1000` by default. If your data folder belongs to someone else, set `PUID` and `PGID` in `/opt/genrearr/.env` to the output of `id -u` and `id -g`, then run `cd /opt/genrearr && docker compose up -d`.

### HTTPS / reverse proxy

See the README's **Reverse proxy / HTTPS** section (`TRUST_PROXY=1`, `COOKIE_SECURE=1`).

### View live GenreArr logs

```bash
docker logs -f genrearr
```

Press **Ctrl+C** to stop viewing logs; this does not stop GenreArr.

### Restart GenreArr

```bash
cd /opt/genrearr
docker compose restart
```

### Update GenreArr

Run the installer again — no Git is needed:

```bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo bash
```

Your `.env`, `docker-compose.override.yml` and the `/opt/genrearr/data` directory (database) are kept, so updating does not erase your configuration. Files removed from GenreArr in a newer version are cleaned up automatically.

### Forgot the admin password

Edit `ADMIN_PASSWORD=` in `/opt/genrearr/.env` to a new value, then run `cd /opt/genrearr && docker compose up -d`. A changed `ADMIN_PASSWORD` in `.env` always replaces the current password.

## Safe first-time checklist

- [ ] Saved the generated admin password (or changed it in **Settings → Admin password**)
- [ ] Added Radarr/Sonarr successfully
- [ ] Test + roots works
- [ ] Rules use exact Arr-visible root paths
- [ ] Dry Run is enabled
- [ ] Planner destinations look correct
- [ ] One movie was tested successfully
- [ ] One series was tested successfully
- [ ] Webhook was tested
- [ ] Configuration was backed up
- [ ] Only then consider scheduled/live bulk sorting

## Need help?

When reporting a problem, include:

- GenreArr version
- Radarr or Sonarr version
- Docker/non-Docker setup
- What you expected
- What happened
- Relevant GenreArr logs

**Never include API keys, passwords, webhook secrets, or other private credentials.**

Support development: https://buymeacoffee.com/kasundigital

Designed & Developed by Kasun Indika — https://www.kasunindika.com
