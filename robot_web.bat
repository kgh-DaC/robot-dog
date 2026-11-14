@echo off
rem Double-click: starts the USB gateway (minimized window) and opens the control page.
rem If the gateway is already running, this only opens the page.
rem Close the minimized "RobotDog gateway" window (or press Ctrl+C in it) to stop it.
cd /d "%~dp0"
start "RobotDog gateway" /min python "esp32-robot-dog-code-master\software\usb_gateway\usb_gateway.py" --open %*
