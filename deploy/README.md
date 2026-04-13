# Linux VPS Deployment Guide

## System Requirements

- Python 3.11+
- ~500MB disk for SQLite database
- 1GB RAM minimum (2GB recommended for extraction)
- Network access to Anthropic API, ACLED, GDELT, NewsAPI

## Setup

```bash
# Clone and setup
git clone https://github.com/ayaan6pc-cpu/geopolitical-risk.git /opt/georisk
cd /opt/georisk

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env  # Add your API keys

# Initialize database
python -c "from utils.db import initialize_db; initialize_db()"

# Download GPR data
python -m scripts.download_gpr

# Test Track A
python -c "
from utils.db import get_connection
from track_a.predict import predict_track_a
from config.settings import load_country_config
conn = get_connection()
r = predict_track_a(load_country_config('nigeria'), conn)
print(f'Nigeria: {r[\"probability\"]:.1%}')
conn.close()
"
```

## Cron Setup

```bash
# Install crontab entries
crontab deploy/crontab.example

# Or add manually:
crontab -e
# Paste contents of deploy/crontab.example
```

## API Server (systemd)

```bash
# Create service user
sudo useradd -r -s /bin/false georisk

# Install service
sudo cp deploy/georisk-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable georisk-api
sudo systemctl start georisk-api

# Check status
sudo systemctl status georisk-api
```

## Monitoring

```bash
# Health check (run after pipeline completes)
python -m scripts.health_check

# Tail today's log
tail -f data/pipeline_logs/daily_$(date +%Y-%m-%d).log

# Check DB size
du -sh data/geopolitical.db

# View latest predictions
curl -s localhost:8000/countries | python -m json.tool

# View alerts
curl -s localhost:8000/alerts | python -m json.tool
```

## Nginx Reverse Proxy (optional)

```nginx
server {
    listen 80;
    server_name georisk.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
