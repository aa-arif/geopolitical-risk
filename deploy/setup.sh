#!/usr/bin/env bash
#
# Precursion VPS Setup Script
# Tested on Ubuntu 24.04 LTS (DigitalOcean, 2GB RAM)
#
# Usage:
#   1. scp this project to the VPS:  rsync -az --exclude=venv . root@YOUR_IP:/opt/georisk/
#   2. ssh root@YOUR_IP
#   3. bash /opt/georisk/deploy/setup.sh
#   4. cp /opt/georisk/.env.example /opt/georisk/.env && nano /opt/georisk/.env
#   5. systemctl restart georisk-api
#
set -euo pipefail

INSTALL_DIR="/opt/georisk"
DOMAIN="${DOMAIN:-precursion.io}"

echo "=== Precursion VPS Setup ==="
echo "Install dir: $INSTALL_DIR"
echo "Domain:      $DOMAIN"
echo ""

# --- System packages ---
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv python3-pip nginx git curl ufw

# --- Firewall ---
echo "[2/8] Configuring firewall..."
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# --- System user ---
echo "[3/8] Creating georisk user..."
if ! id -u georisk &>/dev/null; then
    useradd -r -s /bin/false -d "$INSTALL_DIR" georisk
fi
chown -R georisk:georisk "$INSTALL_DIR"

# --- Python venv ---
echo "[4/8] Setting up Python virtual environment..."
cd "$INSTALL_DIR"
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "Python packages installed."

# --- Initialize database ---
echo "[5/8] Initializing database..."
if [ ! -f "$INSTALL_DIR/data/geopolitical.db" ]; then
    python -c "from utils.db import initialize_db; initialize_db()"
    echo "Database initialized."
else
    echo "Database already exists, skipping."
fi

# Download GPR data if missing
if [ ! -d "$INSTALL_DIR/data/gpr" ] || [ -z "$(ls -A "$INSTALL_DIR/data/gpr" 2>/dev/null)" ]; then
    python -m scripts.download_gpr || echo "GPR download failed (non-critical, Track A will use fallback)."
fi

# --- Nginx ---
echo "[6/8] Configuring nginx..."
# Generate config from template
sed "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" "$INSTALL_DIR/deploy/nginx.conf" \
    > /etc/nginx/sites-available/georisk

ln -sf /etc/nginx/sites-available/georisk /etc/nginx/sites-enabled/georisk
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl reload nginx
echo "Nginx configured for $DOMAIN."

# --- Systemd service ---
echo "[7/8] Installing systemd service..."
cp "$INSTALL_DIR/deploy/georisk-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable georisk-api
echo "Systemd service installed."

# --- Cron jobs ---
echo "[8/8] Installing cron jobs..."
crontab -u georisk "$INSTALL_DIR/deploy/crontab.example"
echo "Cron jobs installed for georisk user."

# --- Permissions ---
chown -R georisk:georisk "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true

echo ""
echo "========================================="
echo "  Setup complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo ""
echo "  1. Configure your API keys:"
echo "     cp $INSTALL_DIR/.env.example $INSTALL_DIR/.env"
echo "     nano $INSTALL_DIR/.env"
echo ""
echo "  2. Start the API server:"
echo "     systemctl start georisk-api"
echo "     systemctl status georisk-api"
echo ""
echo "  3. Test it:"
echo "     curl http://localhost:8000/api/health"
echo "     curl http://$DOMAIN/api/health"
echo ""
echo "  4. Set up DNS + TLS (see below)"
echo ""
echo "  5. Run the pipeline once:"
echo "     cd $INSTALL_DIR && sudo -u georisk venv/bin/python -m pipeline.daily_run"
echo ""
echo "========================================="
echo "  DNS + TLS Setup (Cloudflare)"
echo "========================================="
echo ""
echo "  Option A: Cloudflare Proxy (recommended, free TLS)"
echo "    1. Add domain to Cloudflare"
echo "    2. Create A record: $DOMAIN -> $(curl -s ifconfig.me)"
echo "    3. Create A record: www.$DOMAIN -> $(curl -s ifconfig.me)"
echo "    4. Set SSL mode to 'Full' in Cloudflare dashboard"
echo "    5. Orange-cloud (proxy) both records"
echo "    6. Done - HTTPS works automatically"
echo ""
echo "  Option B: Let's Encrypt (certbot)"
echo "    apt install certbot python3-certbot-nginx"
echo "    certbot --nginx -d $DOMAIN -d www.$DOMAIN"
echo "    # Auto-renewal is installed by certbot"
echo ""
echo "  After TLS is working, update .env:"
echo "    CORS_ORIGINS=https://$DOMAIN,https://www.$DOMAIN"
echo ""
echo "  Public page: https://$DOMAIN/public"
echo "  Dashboard:   https://$DOMAIN/"
echo "  API health:  https://$DOMAIN/api/health"
echo ""
