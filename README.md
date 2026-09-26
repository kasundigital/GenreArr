# 🎬 GenreArr

**Smart library routing for Radarr & Sonarr.** GenreArr evaluates imported media and asks the Arr API to move it to the correct root folder. It does not blindly move files at filesystem level.

## ✨ v0.5.0

### Routing
- Multiple Radarr and Sonarr instances (edit, enable/disable, regenerate webhook URL)
- Rules for all instances of a type, or limited to one instance (e.g. separate HD and 4K Radarr)
- Genre rules with ALL / ANY matching
- Combined genres such as `Animation + Family`
- Smart conditions: original language (`ja` or `Japanese`, several allowed), year-before, Arr tag ID
- Priority ordering, enable/disable/edit rules
- Separate movie/series fallback roots
- Title/path/tag exclusions

### Safety
- Dry Run enabled by default
- Library Planner with Current → Proposed paths, search, filters and paging
- Selective bulk moves
- Arr root-folder validation
- Minimum destination free-space validation
- Queue/import protection
- Destination path collision detection
- Post-move API path verification
- Configurable automatic retries
- Radarr/Sonarr file-move task confirmation (failed moves are reported, not retried)
- Free space tracked across a bulk move
- Rule targets checked against Arr root folders when saved
- Optional path mappings to detect destination folders on disk that Radarr/Sonarr do not know about
- Move history + Undo of the latest move per title

### Automation & operations
- Per-instance Radarr/Sonarr webhook
- Last-webhook health status
- Scheduled reconciliation scan
- Scan progress + Stop
- System Health page
- Discord/Telegram notifications with a test button
- Configuration export + restore
- Jobs survive restarts; repeated scan results are de-duplicated and old preview history is pruned
- Docker/Compose running as a non-root user (`PUID`/`PGID`) with a healthcheck
- Admin login (hashed password, changeable in Settings, CSRF-protected forms, rate limited), responsive dark/light UI

## 👋 New to GenreArr?

No Docker/Radarr development knowledge is required. Follow the **[Beginner Setup Guide](docs/SETUP.md)** for a step-by-step walkthrough covering:

- Installation and first login
- Finding your Radarr/Sonarr API key
- Adding instances
- Understanding Docker/root-folder paths
- Creating simple and Smart Rules
- Dry Run and Library Planner
- Testing your first real move safely
- Radarr/Sonarr webhook setup
- Health checks and scheduled reconciliation
- Backup/restore, updates and troubleshooting

**New users: please read the Setup Guide before enabling live moves.**

## 🚀 Easy install — no Git required

For most users, install GenreArr with **one command**:

```bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo bash
```

That's it. The installer downloads GenreArr, creates a secure random admin password and secret, starts the Docker container, and prints the web address and password.

Default web port:

```text
http://YOUR-SERVER-IP:3033
```

GenreArr data is stored in `/opt/genrearr/data`.

The installer uses the latest GitHub release. To pin a version, or to try the development branch:

```bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo GENREARR_VERSION=v0.5.0 bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo GENREARR_VERSION=main bash
```

### Update later

Run the same command again:

```bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo bash
```

The existing `.env` and persistent `data` directory are retained.

> Docker and the Docker Compose plugin must already be installed. No `git clone` is required.

## 🔒 Reverse proxy / HTTPS

GenreArr does not serve HTTPS itself. Put it behind nginx, Caddy or Traefik and add to `/opt/genrearr/.env`:

```text
TRUST_PROXY=1
COOKIE_SECURE=1
```

`TRUST_PROXY=1` makes login rate limiting use the real client IP; `COOKIE_SECURE=1` only sends the login cookie over HTTPS. Then run `cd /opt/genrearr && docker compose up -d`.

## 🔗 Webhook

Add the instance in GenreArr. Copy its unique webhook path and add it in **Radarr/Sonarr → Settings → Connect → Webhook** using the GenreArr base URL. Enable download/import events.

## 🧠 Examples

| Priority | Media | Genre | Extra | Destination |
|---:|---|---|---|---|
| 10 | Movie | Animation + Family | — | `/movies/Kids` |
| 20 | Movie | Animation | language = ja | `/movies/Anime` |
| 30 | Movie | — | year < 1980 | `/movies/Classics` |
| 40 | Movie | Documentary | — | `/movies/Documentary` |

## 🧪 Recommended test sequence

1. Connect a test Radarr instance.
2. Open **Health** and verify API/root discovery.
3. Create rules using exact roots returned by Radarr.
4. Open **Planner** and inspect decisions.
5. Run Preview/Dry Run.
6. Select one small test movie and apply the move.
7. Confirm both the physical media path and Radarr's displayed path.
8. Test webhook import sorting.
9. Repeat for Sonarr before production use.

## 🛠️ Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The tests run GenreArr against a small fake Radarr. Both Radarr and Sonarr are covered. GitHub Actions runs them, plus a Docker build and container check, on every push and pull request.

## ☕ Support

GenreArr is free and open source. If it helps you, you can support continued development:

<a href="https://buymeacoffee.com/kasundigital" target="_blank">
  <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="60">
</a>

**[☕ Support Kasun on Buy Me a Coffee](https://buymeacoffee.com/kasundigital)**

Designed & Developed by **Kasun Indika** — https://www.kasunindika.com
