@echo off
rem CloudWatch Monitor - inicializador para Windows.
rem Verifica/instala: Python 3.10+, dependencias (requirements.txt), AWS CLI (opcional);
rem roda o assistente de configuracao na 1a vez e abre o painel.
setlocal EnableExtensions
chcp 65001 >nul
title CloudWatch Monitor
cd /d "%~dp0"

echo.
echo  ==============================================
echo    CloudWatch Monitor - verificando requisitos
echo  ==============================================
echo.

rem ---------------------------------------------------------------- 1. Python
echo [1/4] Python 3.10 ou mais novo
call :find_python
if not defined PYEXE (
    echo       Nao encontrado.
    where winget >nul 2>nul
    if errorlevel 1 goto :python_manual
    choice /C SN /M "      Instalar o Python 3.12 agora (winget)"
    if errorlevel 2 goto :python_manual
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    call :find_python
)
if not defined PYEXE goto :python_manual
for /f "delims=" %%V in ('call "%PYEXE%" -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%V"
echo       OK - Python %PYVER%  (%PYEXE%)

rem ---------------------------------------------------------------- 2. Dependencias
echo [2/4] Dependencias (requirements.txt)
rem --user evita precisar de administrador; dentro de um .venv nao se usa --user
set "PIPUSER=--user"
if exist ".venv\Scripts\python.exe" set "PIPUSER="
"%PYEXE%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo       pip ausente - instalando...
    "%PYEXE%" -m ensurepip --upgrade >nul 2>nul
)
"%PYEXE%" -c "import boto3" >nul 2>nul
if errorlevel 1 (
    echo       Instalando dependencias...
    "%PYEXE%" -m pip install %PIPUSER% --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :deps_failed
)
rem garante as versoes minimas do requirements.txt (rapido quando ja esta tudo certo)
"%PYEXE%" -m pip install %PIPUSER% --disable-pip-version-check --quiet -r requirements.txt >nul 2>nul
for /f "delims=" %%V in ('call "%PYEXE%" -c "import boto3; print(boto3.__version__)"') do set "BOTOVER=%%V"
if not defined BOTOVER goto :deps_failed
echo       OK - boto3 %BOTOVER%

rem ---------------------------------------------------------------- 3. AWS CLI (opcional)
echo [3/4] AWS CLI (opcional - so e necessario para login SSO)
set "AWSCLI="
for /f "delims=" %%A in ('where aws 2^>nul') do if not defined AWSCLI set "AWSCLI=%%A"
if not defined AWSCLI if exist "%ProgramFiles%\Amazon\AWSCLIV2\aws.exe" set "AWSCLI=%ProgramFiles%\Amazon\AWSCLIV2\aws.exe"
if defined AWSCLI (
    for /f "tokens=1" %%V in ('call "%AWSCLI%" --version 2^>^&1') do echo       OK - %%V
) else (
    echo       Nao instalado. Sem problema: o assistente cadastra a chave de acesso sem ele.
    if not exist ".env" (
        where winget >nul 2>nul
        if not errorlevel 1 (
            choice /C SN /T 20 /D N /M "      Instalar o AWS CLI mesmo assim (recomendado se a empresa usa SSO)"
            if not errorlevel 2 winget install -e --id Amazon.AWSCLI --accept-package-agreements --accept-source-agreements
        )
    )
)

rem ---------------------------------------------------------------- 4. Configuracao
echo [4/4] Configuracao
if exist ".env" (
    echo       OK - usando .env  ^(para mudar: "%PYEXE%" configurar.py^)
) else (
    echo       Primeira execucao: vamos configurar.
    "%PYEXE%" configurar.py --if-needed
    if errorlevel 1 (
        echo.
        echo Configuracao cancelada. Rode o iniciar.bat de novo quando quiser.
        pause
        exit /b 1
    )
)

echo.
echo Iniciando o painel... (feche esta janela ou Ctrl+C para parar)
echo.
rem MONITOR_LAUNCHER=1: o painel pode pedir para ser reiniciado (codigo 3) quando o codigo muda
set "MONITOR_LAUNCHER=1"
:run_app
"%PYEXE%" app.py %*
if "%ERRORLEVEL%"=="3" (
    echo.
    echo Codigo atualizado - reiniciando o painel...
    echo.
    set "MONITOR_RELOADED=1"
    goto run_app
)
if errorlevel 1 pause
exit /b

rem ================================================================ rotinas
:find_python
set "PYEXE="
for %%P in (".venv\Scripts\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" "%ProgramFiles%\Python313\python.exe" "%ProgramFiles%\Python312\python.exe" "%ProgramFiles%\Python311\python.exe" "%ProgramFiles%\Python310\python.exe") do (
    if not defined PYEXE if exist "%%~P" (
        "%%~P" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PYEXE=%%~P"
    )
)
if defined PYEXE exit /b 0
rem python do PATH (ignora o atalho da Microsoft Store, que falha nesse teste)
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if not errorlevel 1 for /f "delims=" %%E in ('python -c "import sys; print(sys.executable)"') do set "PYEXE=%%E"
if defined PYEXE exit /b 0
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if not errorlevel 1 for /f "delims=" %%E in ('py -3 -c "import sys; print(sys.executable)"') do set "PYEXE=%%E"
exit /b 0

:python_manual
echo.
echo [ERRO] Python 3.10+ nao encontrado.
echo        Instale pelo site (vou abrir a pagina) e, na primeira tela do instalador,
echo        MARQUE a opcao "Add python.exe to PATH". Depois rode o iniciar.bat de novo.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:deps_failed
echo.
echo [ERRO] Nao consegui instalar as dependencias (requirements.txt).
echo        Verifique a internet/proxy e tente:  "%PYEXE%" -m pip install --user -r requirements.txt
pause
exit /b 1
