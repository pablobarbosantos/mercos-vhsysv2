@echo off
setlocal

set LOG=C:\mercos_vhsys_git\logs\startup.log
set DIR=C:\mercos_vhsys_git
set PYTHON=C:\Users\rdpadmin\AppData\Local\Python\pythoncore-3.14-64\python.exe

:: Garante pasta de logs
if not exist "%DIR%\logs" mkdir "%DIR%\logs"

echo [%DATE% %TIME%] Iniciando sistema Mercos-VHSys >> "%LOG%"

:: --- Node (WhatsApp server) ---
for /f "delims=" %%i in ('where node 2^>nul') do set NODE=%%i
if not defined NODE (
    echo [%DATE% %TIME%] ERRO: node nao encontrado no PATH >> "%LOG%"
) else (
    echo [%DATE% %TIME%] Node encontrado: %NODE% >> "%LOG%"
    start "WhatsApp Server" /min /d "%DIR%\whatsapp_server" cmd /c ""%NODE%" server.js >> "%DIR%\logs\node.log" 2>&1"
)

:: Aguarda Node inicializar
timeout /t 5 /nobreak >nul

:: --- Tailscale Funnel ---
where tailscale >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [%DATE% %TIME%] AVISO: tailscale nao encontrado no PATH >> "%LOG%"
) else (
    echo [%DATE% %TIME%] Ativando Tailscale Funnel porta 8000 >> "%LOG%"
    tailscale funnel 8000 >> "%LOG%" 2>&1
    echo [%DATE% %TIME%] Tailscale Funnel ativo >> "%LOG%"
)

:: --- Python (FastAPI) ---
echo [%DATE% %TIME%] Iniciando Python/FastAPI >> "%LOG%"
set PYTHONIOENCODING=utf-8
"%PYTHON%" "%DIR%\main.py" >> "%DIR%\logs\python.log" 2>&1

echo [%DATE% %TIME%] Python encerrado >> "%LOG%"
endlocal
