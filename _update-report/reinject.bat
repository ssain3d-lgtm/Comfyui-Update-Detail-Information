@echo off
:: Re-add the report hook to the update .bat files after an updater overwrote them.
call "%~dp0..\Update-Report.bat" --reinject
