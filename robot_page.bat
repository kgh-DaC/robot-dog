@echo off
rem Double-click: opens the control page straight from disk in Microsoft Edge.
rem No gateway program is needed. On the page choose "USB serial" (the browser
rem opens the serial port itself) or "Wi-Fi" (shows the robot's own page).
rem Chrome works too: open the same index.html file in Chrome.
start "" msedge "%~dp0esp32-robot-dog-code-master\software\robot_control_web\index.html"
