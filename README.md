# 🎬 GenreArr

**Genre-based library routing for Radarr & Sonarr — safely managed through the Arr APIs.**

GenreArr watches imported movies and series, evaluates configurable genre rules, and asks Radarr/Sonarr to move media into the correct root folder. It never uses a blind filesystem `mv`, so the Arr database remains authoritative.

## ✨ v0.2.0 features

- 🎥 Radarr + 📺 Sonarr, with multiple instances
- 🔔 Per-instance webhook endpoint for post-import sorting
- 🔎 Root-folder discovery directly from Arr
- 🧠 Priority rule engine with `ALL` / `ANY` and combined genres (`Animation + Family`)
- 🛟 Dry Run enabled by default
- 🧪 Bulk preview and live scans with progress + Stop
- 🧱 Import/download queue protection
- 🚫 Title/path/tag exclusions
- 📁 Movie and series fallback roots
- ✅ Post-move path verification
- ↩️ Undo for GenreArr move history
- 🧾 Decision history with **Why?** explanation
- 🔔 Optional Discord / Telegram notifications
- 💾 Configuration JSON export
- 🌗 Dark/light UI, mobile responsive
- 🔐 Admin-only UI
- 🐳 Docker / Compose

## 🚀 Quick start

```bash
git clone https://github.com/kasundigital/GenreArr.git
cd GenreArr
docker compose up -d --build
```

Open `http://YOUR-SERVER:3033`. Default password is `admin`; **change `ADMIN_PASSWORD` and `SECRET_KEY` in `docker-compose.yml` before exposing GenreArr.**

## 🔗 Webhook setup

After adding an instance, GenreArr shows a unique webhook path. In Radarr/Sonarr go to **Settings → Connect → Webhook**, use GenreArr's reachable base URL plus that path, and enable download/import events. GenreArr will process the affected title after the Arr import event.

## 🧠 Example rules

| Priority | Media | Match | Genres | Destination |
|---:|---|---|---|---|
| 10 | Movie | ALL | Animation + Family | `/movies/Kids` |
| 20 | Movie | ALL | Documentary | `/movies/Documentary` |
| 30 | Movie | ALL | Horror | `/movies/Horror` |
| 10 | Series | ALL | Animation | `/tv/Animation` |

Lower priority numbers win. A fallback root can be configured separately for movies and series.

## ⚠️ Safety

Start with **Dry Run**. Review the Current → Destination paths and decision reasons before applying moves to a production library. GenreArr skips media seen in the Arr queue and verifies the path after a move request. Always maintain normal backups of your Arr configuration/database.

## ☕ Support

GenreArr is free and open source. If it saves you time, support development through Buy Me a Coffee: https://buymeacoffee.com/kasundigital

Designed & Developed by **Kasun Indika** — https://www.kasunindika.com
