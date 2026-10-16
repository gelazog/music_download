@echo off
setlocal EnableDelayedExpansion
title ytdown - lanzador portable
cd /d "%~dp0"

REM  Uso:
REM    iniciar.bat          revisa los requisitos, pregunta antes de instalar
REM    iniciar.bat /y       instala lo que falte sin preguntar
REM    iniciar.bat /check   solo revisa y sale, no abre la app
REM    iniciar.bat /debug   abre la app con registro detallado

set "AUTO="
set "SOLO_CHECK="
set "APP_ARGS="
for %%A in (%*) do (
    if /i "%%~A"=="/y"      set "AUTO=1"
    if /i "%%~A"=="/s"      set "AUTO=1"
    if /i "%%~A"=="/check"  set "SOLO_CHECK=1"
    if /i "%%~A"=="/debug"  set "APP_ARGS=--debug"
)

set "FALTA="
set "OPCIONAL="
set "PY="

echo.
echo  ==========================================================
echo    ytdown  -  comprobando que este todo listo
echo  ==========================================================
echo.
if defined AUTO echo  [modo /y] Se instalara lo que falte sin preguntar.
if defined AUTO echo.

REM ============================================================
REM  1) Python
REM ============================================================
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo  [X] Python no esta instalado.
    echo      Es el lenguaje en el que esta escrita la app: sin el no arranca.
    echo.
    call :preguntar "      Quieres que lo instale ahora con winget?"
    if errorlevel 2 (
        echo      De acuerdo, no se instala nada.
        call :python_manual
        exit /b 1
    )
    where winget >nul 2>&1
    if errorlevel 1 (
        echo      [X] winget no esta disponible en este Windows.
        call :python_manual
        exit /b 1
    )
    echo      Instalando Python 3.12, esto tarda un par de minutos...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
    if not defined PY (
        echo.
        echo      [!] Python quedo instalado, pero esta ventana todavia no lo ve.
        echo          Cierra esta ventana y vuelve a ejecutar iniciar.bat.
        echo.
        pause
        exit /b 1
    )
)

for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo  [OK] %%v

REM ============================================================
REM  2) pip
REM ============================================================
%PY% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo  [..] pip no encontrado, activandolo con ensurepip...
    %PY% -m ensurepip --upgrade >nul 2>&1
)
%PY% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo  [X] pip no funciona. Sin el no se pueden instalar las librerias.
    echo      Prueba a mano:  %PY% -m ensurepip --upgrade
    set "FALTA=1"
) else (
    echo  [OK] pip
)

REM ============================================================
REM  3) tkinter  (viene con Python, no se instala con pip)
REM ============================================================
%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo  [X] Falta tkinter, que es lo que dibuja la ventana.
    echo      No se puede instalar con pip: reinstala Python desde python.org
    echo      y marca la casilla "tcl/tk and IDLE" durante la instalacion.
    set "FALTA=1"
) else (
    echo  [OK] tkinter
)

REM ============================================================
REM  4) Librerias de Python
REM ============================================================
call :pkg customtkinter "customtkinter"          "la interfaz de la app"
call :pkg yt_dlp        "yt-dlp[default]"        "el motor de descarga"
call :pkg yt_dlp_ejs    "yt-dlp[default]"        "desbloquea todos los formatos de YouTube"
call :pkg curl_cffi     "curl_cffi>=0.10,<0.16"  "disfraza las peticiones para evitar el bot-check"
call :pkg PIL           "Pillow"                 "miniaturas y portadas"
call :pkg requests      "requests"               "descarga de miniaturas"

REM ============================================================
REM  5) FFmpeg  (obligatorio: convierte y une audio/video)
REM ============================================================
if exist "%~dp0ffmpeg.exe" (
    set "PATH=%~dp0;!PATH!"
    echo  [OK] FFmpeg portable, aqui mismo junto al script
    goto ffmpeg_listo
)
where ffmpeg >nul 2>&1
if not errorlevel 1 (
    echo  [OK] FFmpeg, encontrado en el PATH del sistema
    goto ffmpeg_listo
)

echo  [X] Falta FFmpeg.
echo      Es lo que convierte a MP3 y lo que une video y audio.
echo      Sin el, las descargas se quedan a medias.
echo.
call :preguntar "      Lo instalo con winget?"
if errorlevel 2 (
    call :ffmpeg_manual
    set "FALTA=1"
    goto ffmpeg_listo
)
where winget >nul 2>&1
if errorlevel 1 (
    echo      [X] winget no esta disponible en este Windows.
    call :ffmpeg_manual
    set "FALTA=1"
    goto ffmpeg_listo
)
winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
where ffmpeg >nul 2>&1
if not errorlevel 1 (
    echo  [OK] FFmpeg instalado
) else (
    echo  [!] FFmpeg quedo instalado pero esta ventana aun no lo ve.
    echo      Cierra esta ventana y vuelve a ejecutar iniciar.bat.
    set "FALTA=1"
)
:ffmpeg_listo

REM ============================================================
REM  6) Runtime JS: Deno o Node  (recomendado, no obligatorio)
REM     yt-dlp lo usa para resolver los retos de YouTube: con el
REM     hay todos los formatos y menos bloqueos, sin cookies.
REM ============================================================
if exist "%~dp0deno.exe" (
    echo  [OK] Deno portable, aqui mismo junto al script
    goto js_listo
)
where deno >nul 2>&1
if not errorlevel 1 (
    echo  [OK] Deno
    goto js_listo
)
where node >nul 2>&1
if not errorlevel 1 (
    echo  [OK] Node.js, sirve igual que Deno
    goto js_listo
)

echo  [ ] No hay Deno ni Node.js  ^(opcional^)
echo      Con uno de los dos, YouTube ofrece todos los formatos y bloquea
echo      mucho menos. Sin ellos la app funciona, pero con menos calidades.
echo.
call :preguntar "      Instalo Deno con winget?"
if errorlevel 2 (
    echo      Vale, se sigue sin el. Puedes instalarlo cuando quieras con:
    echo         winget install DenoLand.Deno
    set "OPCIONAL=1"
    goto js_listo
)
where winget >nul 2>&1
if errorlevel 1 (
    echo      [!] winget no esta disponible. Copia deno.exe junto a este script.
    set "OPCIONAL=1"
    goto js_listo
)
winget install -e --id DenoLand.Deno --accept-source-agreements --accept-package-agreements
where deno >nul 2>&1
if not errorlevel 1 (
    echo  [OK] Deno instalado
) else (
    echo  [!] Deno quedo instalado pero esta ventana aun no lo ve.
    echo      Cierra y vuelve a ejecutar iniciar.bat para aprovecharlo.
    set "OPCIONAL=1"
)
:js_listo

REM ============================================================
REM  Resumen
REM ============================================================
echo.
if defined FALTA (
    echo  ==========================================================
    echo    Falta algo imprescindible. Mira las lineas marcadas [X]
    echo    aqui arriba: cada una dice como resolverlo.
    echo  ==========================================================
    echo.
    pause
    exit /b 1
)

if defined SOLO_CHECK (
    echo  Todo correcto. Ejecuta iniciar.bat sin argumentos para abrir la app.
    echo.
    pause
    exit /b 0
)

if defined OPCIONAL (
    echo  Todo lo imprescindible esta listo ^(falta algun opcional^).
) else (
    echo  Todo listo.
)
echo  Abriendo ytdown...
echo.

%PY% main1_new.py %APP_ARGS%
if errorlevel 1 (
    echo.
    echo  ==========================================================
    echo    La app se cerro con un error. El detalle esta aqui arriba.
    echo    Si no se entiende, ejecuta:  iniciar.bat /debug
    echo  ==========================================================
    echo.
    pause
)
exit /b 0


REM ============================================================
REM  Subrutinas
REM ============================================================

REM  :preguntar <mensaje>   -> errorlevel 1 = si, 2 = no
:preguntar
if defined AUTO exit /b 1
choice /c SN /n /m "%~1 [S/N] "
exit /b %errorlevel%

REM  :pkg <modulo> <spec_pip> <para_que_sirve>
REM  El spec puede llevar > y < (redirecciones en batch): siempre entrecomillado.
:pkg
%PY% -c "import %~1" >nul 2>&1
if not errorlevel 1 (
    echo  [OK] %~1
    goto :eof
)
echo  [ ] Falta %~1 - %~3
call :preguntar "      Lo instalo?"
if errorlevel 2 (
    echo      Omitido. La app puede fallar sin el.
    set "FALTA=1"
    goto :eof
)
echo      Instalando %~2 ...
%PY% -m pip install "%~2" --quiet --disable-pip-version-check
%PY% -c "import %~1" >nul 2>&1
if not errorlevel 1 (
    echo  [OK] %~1 instalado
) else (
    echo  [X] No se pudo instalar %~2. Pruebalo a mano:
    echo      %PY% -m pip install "%~2"
    set "FALTA=1"
)
goto :eof

:python_manual
echo.
echo      Para instalarlo a mano:
echo        1. Descargalo de  https://www.python.org/downloads/
echo        2. MUY IMPORTANTE: marca "Add python.exe to PATH" al instalar.
echo        3. Vuelve a ejecutar iniciar.bat
echo.
start "" https://www.python.org/downloads/
pause
goto :eof

:ffmpeg_manual
echo.
echo      Para instalarlo a mano, la forma mas comoda:
echo        1. Entra en  https://www.gyan.dev/ffmpeg/builds/
echo        2. Descarga el paquete "release essentials".
echo        3. Copia ffmpeg.exe y ffprobe.exe junto a este script.
echo      Asi queda portable y no hace falta instalar nada en el sistema.
echo.
goto :eof
