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

curl -fsSL "https://github.com/kasundigital/GenreArr/archive/refs/heads/main.tar.gz" -o "$TMP/genrearr.tar.gz"
tar -xzf "$TMP/genrearr.tar.gz" -C "$TMP"
cp -a "$TMP/GenreArr-main/." "$INSTALL_DIR/"
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
echo "✅ GenreArr is running"
echo "🌐 Open: http://${IP:-YOUR-SERVER}:$PORT"
if [ "$FIRST_INSTALL" = 1 ]; then
  echo "🔐 Admin password: $PASS"
  echo
  echo "Keep this password safe (it is also stored in $INSTALL_DIR/.env). Dry Run is enabled by default."
else
  echo "🔐 Admin password: unchanged (the one you set in Settings, or ADMIN_PASSWORD in $INSTALL_DIR/.env)"
fi
echo "📁 Data: $INSTALL_DIR/data"
