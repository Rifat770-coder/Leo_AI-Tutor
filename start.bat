@echo off
setlocal
pushd "%~dp0" || goto :error
title Leo AI Tutor

set "LEO_PYTHON=.venv\Scripts\python.exe"
if exist "%LEO_PYTHON%" goto :check_dependencies

echo Creating the Python 3.12 virtual environment...
py -3.12 -m venv .venv 2>nul
if not errorlevel 1 goto :check_dependencies
python -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" 2>nul
if errorlevel 1 goto :use_uv
python -m venv .venv
if errorlevel 1 goto :error
goto :check_dependencies

:use_uv
where uv >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.12 or uv, then run start.bat again.
    goto :error
)
uv venv --python 3.12 .venv
if errorlevel 1 goto :error

:check_dependencies
"%LEO_PYTHON%" -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))"
if errorlevel 1 (
    echo The .venv folder must contain a working Python 3.12 environment.
    goto :error
)
"%LEO_PYTHON%" -c "from importlib.util import find_spec; import sys; sys.exit(not all(find_spec(name) for name in ('streamlit', 'crewai', 'dotenv', 'pydantic', 'google.genai')))" >nul 2>&1
if not errorlevel 1 goto :configure

echo Installing project dependencies...
where uv >nul 2>&1
if errorlevel 1 goto :install_with_pip
uv pip install --python "%LEO_PYTHON%" -r requirements.txt
if errorlevel 1 goto :error
goto :configure

:install_with_pip
"%LEO_PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 "%LEO_PYTHON%" -m ensurepip
"%LEO_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :error

:configure
if not exist ".env" (
    copy /-Y ".env.example" ".env" >nul
    if errorlevel 1 goto :error
    echo Created .env. Set LLM_MODEL and LLM_API_KEY before starting a lesson.
)

echo Starting Leo AI Tutor. Press Ctrl+C to stop the server.
"%LEO_PYTHON%" -m streamlit run app.py --server.address=127.0.0.1 --server.headless=false
if errorlevel 1 goto :error
popd
exit /b 0

:error
echo.
echo Leo AI Tutor could not start. Check the message above and try again.
pause
exit /b 1
