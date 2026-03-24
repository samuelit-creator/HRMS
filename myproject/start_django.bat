@echo off
REM Activate virtualenv
call C:\Users\medle\Documents\pys\myenv\Scripts\activate.bat

REM Go to project directory
cd C:\Users\medle\Documents\pys\myproject

REM Run Waitress server and log output
start "" C:\Users\medle\Documents\pys\myenv\Scripts\pythonw.exe waitress_server.py >> server.log 2>&1

