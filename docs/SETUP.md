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

## 1. Install GenreArr

Open a terminal/SSH session on the Docker server:

```bash
git clone https://github.com/kasundigital/GenreArr.git
cd GenreArr
```

Before starting, edit `docker-compose.yml`.

Change these two values:

```yaml
- SECRET_KEY=replace-with-a-long-random-secret
- ADMIN_PASSWORD=choose-a-strong-password
```

Do not leave the password as `admin` if GenreArr is reachable by other people.

Start GenreArr:

```bash
docker compose up -d --build
```

Check that it is running:

```bash
docker compose ps
```

Open this in a browser:

```text
http://YOUR-SERVER-IP:3033
```

Log in using the password you placed in `ADMIN_PASSWORD`.

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

After a completed import, GenreArr can evaluate that title automatically.

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

## 14. Backup your GenreArr settings

Open **Settings → Backup / Restore**.

Use **Export JSON** before making large changes. The export contains GenreArr configuration, so keep it private because instance information may be sensitive.

Use **Restore JSON** to restore rules/settings/exclusions from a compatible GenreArr backup.

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

### View live GenreArr logs

```bash
docker logs -f genrearr
```

Press **Ctrl+C** to stop viewing logs; this does not stop GenreArr.

### Restart GenreArr

```bash
docker compose restart
```

### Update GenreArr

From the GenreArr directory:

```bash
git pull
docker compose up -d --build
```

The `./data:/data` volume stores the GenreArr database so normal container recreation does not intentionally erase it.

## Safe first-time checklist

- [ ] Changed the default admin password
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
