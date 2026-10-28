@echo off
title Moorebot Scout AI + Home Assistant Server (Mini PC HP)
cd /d "%~dp0"
echo ============================================================================
echo   MOOREBOT SCOUT AI HUB + HOME ASSISTANT BRIDGE (Mini PC HP ProDesk)
echo   - Painel Completo:       http://localhost:8085/
echo   - Painel Home Assistant: http://localhost:8085/ha
echo   - Modo Galaxy Watch:     http://localhost:8085/watch
echo ============================================================================

REM Garantir que o modelo leve llama3.2:3b esta disponivel no Ollama
where ollama >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [1/2] A verificar modelo Ollama llama3.2:3b...
    start /B ollama serve >nul 2>nul
)

echo [2/2] A iniciar servidor FastAPI + TCPROS na porta 8085...
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    "%USERPROFILE%\.local\bin\uv.exe" run --python 3.12 --with-requirements requirements.txt uvicorn backend.main:app --host 0.0.0.0 --port 8085
) else (
    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8085
)
pause
