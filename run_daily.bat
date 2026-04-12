@echo off
cd /d C:\Users\arif6\geopolitical-risk
for /f "tokens=*" %%a in (.env) do set %%a
python -m pipeline.daily_run >> data\pipeline_logs\daily_%date:~-4,4%-%date:~-10,2%-%date:~-7,2%.log 2>&1
