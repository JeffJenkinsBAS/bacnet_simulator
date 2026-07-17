@echo off
rem Headless launcher used by install_scheduled_task.bat. Task Scheduler
rem provides no working-directory setting, so this wrapper sets it before
rem starting the simulator. No pause, no browser, no console prompts.
cd /d "%~dp0..\.."
venv\Scripts\python.exe -m app.main
