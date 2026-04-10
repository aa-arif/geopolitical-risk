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
