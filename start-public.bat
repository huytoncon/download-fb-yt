@echo off
rem Chay may chu + Cloudflare Tunnel de nguoi khac dung qua trang GitHub Pages.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-public.ps1"
pause
