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

:: --- ngrok ---
if exist "%DIR%\ngrok.exe" (
    set NGROK=%DIR%\ngrok.exe
) else (
    for /f "delims=" %%i in ('where ngrok 2^>nul') do set NGROK=%%i
)

if not defined NGROK (
    echo [%DATE% %TIME%] ERRO: ngrok nao encontrado >> "%LOG%"
) else (
    echo [%DATE% %TIME%] ngrok encontrado: %NGROK% >> "%LOG%"
    start "ngrok" /min cmd /c ""%NGROK%" http 8000 >> "%DIR%\logs\ngrok.log" 2>&1"
)

:: Aguarda ngrok inicializar
timeout /t 5 /nobreak >nul

:: --- Python (FastAPI) ---
echo [%DATE% %TIME%] Iniciando Python/FastAPI >> "%LOG%"
set PYTHONIOENCODING=utf-8
"%PYTHON%" "%DIR%\main.py" >> "%DIR%\logs\python.log" 2>&1

echo [%DATE% %TIME%] Python encerrado >> "%LOG%"
endlocal
