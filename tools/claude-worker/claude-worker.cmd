@echo off
REM Thin forwarder so `claude-worker` works from any directory once this file
REM (or its folder) is on PATH.  Passes all arguments through to the PowerShell
REM launcher.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0claude-worker.ps1" %*
