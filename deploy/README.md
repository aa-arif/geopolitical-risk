# Precursion - VPS Deployment Guide

## Quick Start

```bash
# From your local machine:
rsync -az --exclude=venv --exclude=node_modules --exclude=.git \
  . root@YOUR_VPS_IP:/opt/georisk/

# On the VPS:
ssh root@YOUR_VPS_IP
bash /opt/georisk/deploy/setup.sh
cp /opt/georisk/.env.example /opt/georisk/.env
nano /opt/georisk/.env   # Add your API keys
systemctl start georisk-api
```

## System Requirements

- Ubuntu 24.04 LTS (DigitalOcean $6-12/month droplet)
- Python 3.11+
- 2GB RAM recommended
- ~500MB disk for SQLite database
- Network access to: Anthropic API, ACLED, GDELT, NewsAPI

## What the Setup Script Does

1. Installs Python 3.12, nginx, ufw
2. Configures firewall (SSH + HTTP/HTTPS)
3. Creates `georisk` system user
4. Sets up Python venv and installs requirements
5. Initializes the SQLite database
6. Configures nginx as reverse proxy
7. Installs systemd service for the API
8. Installs cron jobs (daily pipeline + weekly digest + health check)

## Architecture

```
Internet -> Cloudflare (TLS) -> nginx (port 80) -> uvicorn (port 8000)
                                                      |
                                                      ├── /api/*     -> FastAPI routes
                                                      ├── /assets/*  -> Static JS/CSS
                                                      └── /*         -> React SPA (index.html)
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for Track B ensemble |
| `ACLED_EMAIL` | Yes | ACLED data access |
| `ACLED_PASSWORD` | Yes | ACLED data access |
| `NEWSAPI_KEY` | Yes | NewsAPI key (supplementary ingestion) |
| `CORS_ORIGINS` | No | Comma-separated allowed origins (default: localhost) |
| `DOMAIN` | No | Domain for nginx config (default: precursion.io) |

## Cron Schedule

| Time | Task |
|------|------|
| 6:00 AM daily | Full prediction pipeline (15 countries) |
| 9:00 AM daily | Health check |
| 10:00 AM Sunday | Weekly digest |

## Monitoring

```bash
# Service status
systemctl status georisk-api

# API health
curl -s localhost:8000/api/health | python3 -m json.tool

# Today's pipeline log
tail -f /opt/georisk/data/pipeline_logs/daily_$(date +%Y-%m-%d).log

# DB size
du -sh /opt/georisk/data/geopolitical.db
```

## Updating

```bash
# From local machine:
rsync -az --exclude=venv --exclude=node_modules --exclude=.git \
  --exclude=data --exclude=.env \
  . root@YOUR_VPS_IP:/opt/georisk/

# On VPS:
cd /opt/georisk
source venv/bin/activate
pip install -r requirements.txt
systemctl restart georisk-api
```

## DNS + TLS (Cloudflare, recommended)

1. Add your domain to Cloudflare
2. Create A record pointing to your VPS IP
3. Enable Cloudflare proxy (orange cloud)
4. Set SSL mode to "Full"
5. Update `.env`: `CORS_ORIGINS=https://precursion.io,https://www.precursion.io`
6. `systemctl restart georisk-api`

## URLs

| Path | Description |
|------|-------------|
| `/public` | Read-only public predictions page (for Substack) |
| `/` | Full dashboard with country detail views |
| `/evaluation` | Prediction accuracy and track comparison |
| `/methodology` | System documentation |
| `/api/health` | API health check |
| `/api/countries` | JSON: all countries with current predictions |
