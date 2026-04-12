# Setting Up Daily Scheduled Task (Windows)

1. Open Task Scheduler (search "Task Scheduler" in Start menu)
2. Click "Create Basic Task"
3. Name: "GeoRisk Daily Pipeline"
4. Trigger: Daily, 6:00 AM
5. Action: Start a Program
6. Program: C:\Users\arif6\geopolitical-risk\run_daily.bat
7. Start in: C:\Users\arif6\geopolitical-risk
8. Check "Open the Properties dialog" before finishing
9. In Properties > General: check "Run whether user is logged on or not"
10. In Properties > Settings: check "Run task as soon as possible after a scheduled start is missed"

## What the batch file does

- Sets working directory to the project root
- Loads environment variables from .env (API keys)
- Runs the full daily pipeline (GDELT pre-seed + 15 countries sequential)
- Logs output to data/pipeline_logs/daily_YYYY-MM-DD.log

## Expected runtime

- ~2-2.5 hours for 15 countries
- If scheduled at 6:00 AM, expect completion by ~8:30 AM

## Monitoring

- Check logs: `type data\pipeline_logs\daily_YYYY-MM-DD.log`
- Check for errors: `findstr /i "error\|failed" data\pipeline_logs\daily_YYYY-MM-DD.log`
- View predictions: http://localhost:8000/countries (if API server running)
- View alerts: http://localhost:8000/alerts
