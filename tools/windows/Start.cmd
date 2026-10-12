@echo off
setlocal
chcp 65001 >nul
title Android Car Performance Monitor
pushd "%~dp0"
if not exist "runtime\python\python.exe" (
  echo 请先完整解压 ZIP，然后从解压后的文件夹双击 Start.cmd。
  pause
  popd
  exit /b 1
)
set "PYTHONUTF8=1"
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0launcher.py" %*
set "MONITOR_EXIT=%ERRORLEVEL%"
if not "%MONITOR_EXIT%"=="0" (
  echo.
  echo 启动失败，请查看上方错误和 logs\launcher.log。
  pause
)
popd
exit /b %MONITOR_EXIT%
