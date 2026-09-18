# 🎬 GenreArr

**Smart library routing for Radarr & Sonarr.** GenreArr evaluates imported media and asks the Arr API to move it to the correct root folder. It does not blindly move files at filesystem level.

## ✨ v0.3.0 development build

### Routing
- Multiple Radarr and Sonarr instances
- Genre rules with ALL / ANY matching
- Combined genres such as `Animation + Family`
- Smart conditions: original language, year-before, Arr tag ID
- Priority ordering, enable/disable/edit rules
- Separate movie/series fallback roots
- Title/path/tag exclusions

### Safety
- Dry Run enabled by default
- Library Planner with Current → Proposed paths
- Selective bulk moves
- Arr root-folder validation
- Minimum destination free-space validation
- Queue/import protection
- Destination path collision detection
- Post-move API path verification
- Configurable automatic retries
- Move history + Undo

### Automation & operations
- Per-instance Radarr/Sonarr webhook
- Last-webhook health status
- Scheduled reconciliation scan
- Scan progress + Stop
- System Health page
- Discord/Telegram notifications
- Configuration export + restore
- Docker/Compose, admin login, responsive dark/light UI

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

### Update later

Run the same command again:

```bash
curl -fsSL https://raw.githubusercontent.com/kasundigital/GenreArr/main/install.sh | sudo bash
```

The existing `.env` and persistent `data` directory are retained.

> Docker and the Docker Compose plugin must already be installed. No `git clone` is required.

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

## ☕ Support

GenreArr is free and open source. Support development at https://buymeacoffee.com/kasundigital

Designed & Developed by **Kasun Indika** — https://www.kasunindika.com
