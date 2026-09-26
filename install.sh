#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${GENREARR_DIR:-/opt/genrearr}"
PORT_ARG="${GENREARR_PORT:-}"

command -v docker >/dev/null 2>&1 || { echo "Docker is required. Install Docker first."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose plugin is required."; exit 1; }
if [ -n "$PORT_ARG" ] && ! [[ "$PORT_ARG" =~ ^[0-9]+$ ]]; then echo "GENREARR_PORT must be a number."; exit 1; fi

echo "🎬 Installing GenreArr to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

REPO="kasundigital/GenreArr"
# Install the latest published release; GENREARR_VERSION=v0.5.0 pins one, GENREARR_VERSION=main takes the development branch.
VERSION="${GENREARR_VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1 || true)"
  VERSION="${VERSION:-main}"
fi
if [ "$VERSION" = "main" ]; then URL="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
else URL="https://github.com/$REPO/archive/refs/tags/$VERSION.tar.gz"; fi
echo "⬇️  Downloading GenreArr $VERSION"
curl -fsSL "$URL" -o "$TMP/genrearr.tar.gz"
mkdir -p "$TMP/src"
tar -xzf "$TMP/genrearr.tar.gz" -C "$TMP/src" --strip-components=1
# Remove files that an older version installed but this version no longer ships (never .env or data/).
MANIFEST="$INSTALL_DIR/.genrearr-files"
(cd "$TMP/src" && find . -type f ! -path './data/*' | sed 's|^\./||' | sort) > "$TMP/manifest"
if [ -f "$MANIFEST" ]; then
  comm -23 <(sort "$MANIFEST") "$TMP/manifest" | while IFS= read -r f; do
    case "$f" in ""|.env|data/*|*..*) continue ;; esac
    rm -f -- "$INSTALL_DIR/$f"
  done
fi
cp -a "$TMP/src/." "$INSTALL_DIR/"
cp "$TMP/manifest" "$MANIFEST"
mkdir -p "$INSTALL_DIR/data"

cd "$INSTALL_DIR"
FIRST_INSTALL=0
if [ ! -f .env ]; then
  FIRST_INSTALL=1
  PORT="${PORT_ARG:-3033}"
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || openssl rand -hex 32)"
  PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))' 2>/dev/null || openssl rand -base64 18 | tr -d '\n/+=')"
  cat > .env <<EOF
GENREARR_PORT=$PORT
SECRET_KEY=$SECRET
ADMIN_PASSWORD=$PASS
PUID=${PUID:-1000}
PGID=${PGID:-1000}
EOF
  chmod 600 .env
else
  # Keep the existing configuration; only change the port when one was explicitly requested.
  if [ -n "$PORT_ARG" ]; then
    if grep -q '^GENREARR_PORT=' .env; then sed -i "s/^GENREARR_PORT=.*/GENREARR_PORT=$PORT_ARG/" .env
    else echo "GENREARR_PORT=$PORT_ARG" >> .env; fi
  fi
  PORT="$(grep '^GENREARR_PORT=' .env | tail -n1 | cut -d= -f2-)"
  PORT="${PORT:-3033}"
fi

docker compose up -d --build
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "✅ GenreArr $VERSION is running"
echo "🌐 Open: http://${IP:-YOUR-SERVER}:$PORT"
if [ "$FIRST_INSTALL" = 1 ]; then
  echo "🔐 Admin password: $PASS"
  echo
  echo "Keep this password safe (it is also stored in $INSTALL_DIR/.env). Dry Run is enabled by default."
else
  echo "🔐 Admin password: unchanged (the one you set in Settings, or ADMIN_PASSWORD in $INSTALL_DIR/.env)"
fi
echo "📁 Data: $INSTALL_DIR/data"
