@echo off
setlocal enabledelayedexpansion
title Agentic Universe - Pipeline Backend Runner

echo ========================================================
echo    Agentic Universe - Data Pipeline Backend Runner
echo ========================================================
echo.

:: Check for virtualenv activation if available
if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Activating virtual environment (.venv)...
    call .venv\Scripts\activate.bat
)

:MENU
echo.
echo Please choose an option:
echo   [1] Full Pipeline: Generate Data + Ingest Drop 1 & 2 + Generate Reports
echo   [2] Ingest Drop 1 only
echo   [3] Ingest Drop 2 only
echo   [4] Generate Reports only (--out reports/)
echo   [5] Run All Automated Tests (pytest)
echo   [6] Exit
echo.
set /p CHOICE="Enter choice [1-6]: "

if "%CHOICE%"=="1" goto RUN_ALL
if "%CHOICE%"=="2" goto INGEST_DROP1
if "%CHOICE%"=="3" goto INGEST_DROP2
if "%CHOICE%"=="4" goto REPORT_ONLY
if "%CHOICE%"=="5" goto RUN_TESTS
if "%CHOICE%"=="6" goto EXIT_SCRIPT

echo.
echo [ERROR] Invalid choice. Please select 1 to 6.
goto MENU

:RUN_ALL
echo.
echo ========================================================
echo [STEP 1/4] Generating synthetic data...
echo ========================================================
python assignment-a-data\tools\generate_data.py --seed 42 --scale small --out data
if errorlevel 1 (
    echo [ERROR] Data generation failed!
    pause
    goto MENU
)

echo.
echo ========================================================
echo [STEP 2/4] Ingesting Drop 1...
echo ========================================================
python run.py ingest data\drop_1
if errorlevel 1 (
    echo [ERROR] Ingesting Drop 1 failed!
    pause
    goto MENU
)

echo.
echo ========================================================
echo [STEP 3/4] Ingesting Drop 2...
echo ========================================================
python run.py ingest data\drop_2
if errorlevel 1 (
    echo [ERROR] Ingesting Drop 2 failed!
    pause
    goto MENU
)

echo.
echo ========================================================
echo [STEP 4/4] Generating reports into reports\...
echo ========================================================
python run.py report --out reports
if errorlevel 1 (
    echo [ERROR] Report generation failed!
    pause
    goto MENU
)

echo.
echo ========================================================
echo [SUCCESS] Full pipeline completed successfully!
echo Reports written to folder: reports\
echo ========================================================
pause
goto MENU

:INGEST_DROP1
echo.
echo [INFO] Ingesting data\drop_1...
python run.py ingest data\drop_1
pause
goto MENU

:INGEST_DROP2
echo.
echo [INFO] Ingesting data\drop_2...
python run.py ingest data\drop_2
pause
goto MENU

:REPORT_ONLY
echo.
echo [INFO] Generating reports into reports\...
python run.py report --out reports
pause
goto MENU

:RUN_TESTS
echo.
echo [INFO] Running test suite...
pytest
pause
goto MENU

:EXIT_SCRIPT
echo Exiting...
exit /b 0

