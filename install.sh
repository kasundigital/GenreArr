#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${GENREARR_DIR:-/opt/genrearr}"
PORT="${GENREARR_PORT:-3033}"

command -v docker >/dev/null 2>&1 || { echo "Docker is required. Install Docker first."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose plugin is required."; exit 1; }

echo "🎬 Installing GenreArr to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL "https://github.com/kasundigital/GenreArr/archive/refs/heads/main.tar.gz" -o "$TMP/genrearr.tar.gz"
tar -xzf "$TMP/genrearr.tar.gz" -C "$TMP"
cp -a "$TMP/GenreArr-main/." "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR/data"

cd "$INSTALL_DIR"
if [ ! -f .env ]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || openssl rand -hex 32)"
  PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))' 2>/dev/null || openssl rand -base64 18 | tr -d '\n')"
  cat > .env <<EOF
GENREARR_PORT=$PORT
SECRET_KEY=$SECRET
ADMIN_PASSWORD=$PASS
EOF
  chmod 600 .env
else
  PASS="$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-)"
fi

docker compose up -d --build
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "✅ GenreArr is running"
echo "🌐 Open: http://${IP:-YOUR-SERVER}:$PORT"
echo "🔐 Admin password: $PASS"
echo "📁 Data: $INSTALL_DIR/data"
echo
echo "Keep this password safe. Dry Run is enabled by default."
