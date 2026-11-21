@echo off
rem Command-line version. Example: CLI.bat photo.jpg --mode full
"%~dp0..\python\python.exe" "%~dp0face_circle_blur.py" %*
