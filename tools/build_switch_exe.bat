@echo off
rem FM-1 Switch as one FM1-Switch.exe (Windows). Needs Python 3 from python.org, with Tk (the default).
rem Double-click this file, or run it from the tools folder; the .exe lands in tools\dist.
rem Rescue needs nothing more on Windows: it talks to the FM-1 through the Windows disk driver.
cd /d "%~dp0"
python -m pip install --upgrade mido python-rtmidi pyserial sv-ttk pyinstaller || goto :fail
python -m PyInstaller --noconfirm --onefile --windowed --name FM1-Switch --paths . --hidden-import mido.backends.rtmidi --hidden-import serial.tools.list_ports --collect-data sv_ttk --add-data "..\VERSION;." fm1_switch.pyw || goto :fail
echo.
echo Done: %~dp0dist\FM1-Switch.exe
echo Put it next to the "Firmware Wild" and "Firmware NB303" folders.
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
