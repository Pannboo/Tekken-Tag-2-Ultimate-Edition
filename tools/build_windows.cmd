@echo off
rem Builds the port on Windows: double-click, or drop the game's .wua archive
rem on this file. Needs Python 3 (python.org or the Microsoft Store) and Git;
rem the compiler, the libraries, CMake and Ninja are fetched by the script.
setlocal
cd /d "%~dp0\.."
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 tools\build_windows.py %*
    goto :done
)
where python >nul 2>nul
if %errorlevel% equ 0 (
    python tools\build_windows.py %*
    goto :done
)
echo Python 3 is needed: install it from https://www.python.org/downloads/windows/
echo (tick "Add python.exe to PATH") or from the Microsoft Store, then run this again.
:done
echo.
pause
