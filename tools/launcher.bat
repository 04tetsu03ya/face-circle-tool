@echo off
rem Double-click to open the tool. You can also drop image files onto this file.
start "" "%~dp0python\pythonw.exe" "%~dp0app\gui.py" %*
