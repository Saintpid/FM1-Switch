@echo off
rem FM-1 Switch to the Mac, as the Noisebud apps go: the sources it needs in FM1Switch_mac_ready_vX.zip,
rem with Build_FM1Switch_Mac.command, in the Mac Export folder that syncs to the Mac. There, double-click
rem the .command: it builds FM1-Switch.app and its DMG and puts the DMG in the same folder.
setlocal

set "TOOLS_DIR=%~dp0"
if "%TOOLS_DIR:~-1%"=="\" set "TOOLS_DIR=%TOOLS_DIR:~0,-1%"
for %%I in ("%TOOLS_DIR%\..") do set "PROJECT_DIR=%%~fI"

set "PROJECT_NAME=FM1 Switch"
set "ZIP_SLUG=FM1Switch"
set "EXPORT_ROOT=C:\Users\Johannes\Documents\SynthEdit Projects\Mac Export\SEM"
set /p VERSION=<"%PROJECT_DIR%\VERSION"
set "STAGE_ROOT=%TEMP%\FM1Switch_mac_ready_stage"
set "STAGE_DIR=%STAGE_ROOT%\%PROJECT_NAME%"
set "ZIP_NAME=%ZIP_SLUG%_mac_ready_v%VERSION%.zip"
set "ZIP_PATH=%STAGE_ROOT%\%ZIP_NAME%"

echo Exporting FM-1 Switch Mac zip package...
echo Source:      "%PROJECT_DIR%"
echo Destination: "%EXPORT_ROOT%"

if exist "%STAGE_ROOT%" rmdir /S /Q "%STAGE_ROOT%"
mkdir "%STAGE_DIR%\tools" || exit /b 1
for %%F in (fm1_switch.pyw fm1_install.py fm1_rescue.py build_switch_mac.sh) do (
  copy /Y "%TOOLS_DIR%\%%F" "%STAGE_DIR%\tools\%%F" >nul || (echo ERROR: Could not copy %%F. & exit /b 1)
)
copy /Y "%PROJECT_DIR%\VERSION" "%STAGE_DIR%\VERSION" >nul || exit /b 1
copy /Y "%PROJECT_DIR%\LICENSE" "%STAGE_DIR%\LICENSE" >nul || exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%STAGE_DIR%' -DestinationPath '%ZIP_PATH%' -Force"
if errorlevel 1 (
  echo ERROR: Could not create "%ZIP_PATH%".
  exit /b 1
)

if not exist "%EXPORT_ROOT%" mkdir "%EXPORT_ROOT%"
cmd /c copy /Y "%ZIP_PATH%" "%EXPORT_ROOT%\%ZIP_NAME%" >nul
if errorlevel 1 (
  echo ERROR: Could not copy "%ZIP_NAME%" to "%EXPORT_ROOT%".
  exit /b 1
)

cmd /c copy /Y "%TOOLS_DIR%\Build_FM1Switch_Mac.command" "%EXPORT_ROOT%\Build_FM1Switch_Mac.command" >nul
if errorlevel 1 (
  echo ERROR: Could not copy Build_FM1Switch_Mac.command to "%EXPORT_ROOT%".
  exit /b 1
)

echo.
echo Copied "%ZIP_NAME%" and Build_FM1Switch_Mac.command to "%EXPORT_ROOT%".
echo On the Mac, double-click Build_FM1Switch_Mac.command.
exit /b 0
