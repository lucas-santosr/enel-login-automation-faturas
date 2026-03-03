@echo off
echo =============================================
echo  Enel Login Automation - Setup
echo =============================================

:: Verifica se Python está instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado. Instale em https://python.org
    pause
    exit /b 1
)

:: Cria o ambiente virtual
if not exist ".venv" (
    echo [1/4] Criando ambiente virtual...
    python -m venv .venv
) else (
    echo [1/4] Ambiente virtual ja existe, pulando...
)

:: Ativa e instala dependencias
echo [2/4] Instalando dependencias Python...
call .venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt

:: Verifica FFmpeg
echo [3/4] Verificando FFmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [AVISO] FFmpeg nao encontrado no PATH.
    echo         Necessario para gravacao de audio loopback.
    echo         Instale com:  winget install Gyan.FFmpeg
    echo         Depois reinicie este terminal.
    echo.
) else (
    echo         FFmpeg encontrado. OK.
)

:: Verifica Tesseract
echo [4/4] Verificando Tesseract OCR...
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo         Tesseract encontrado. OK.
) else (
    echo.
    echo [AVISO] Tesseract nao encontrado em C:\Program Files\Tesseract-OCR\
    echo         Baixe o instalador em:
    echo         https://github.com/UB-Mannheim/tesseract/wiki
    echo.
)

:: Cria pasta de faturas
if not exist "faturas" mkdir faturas

echo.
echo =============================================
echo  Setup concluido!
echo.
echo  Proximo passo:
echo    1. Edite enel_login.py e preencha:
echo         EMAIL = "seu_email@exemplo.com"
echo         SENHA = "sua_senha"
echo    2. Ajuste CHROME_VERSION para a sua versao
echo       (abra chrome://settings/help no Chrome)
echo    3. Execute: python enel_login.py
echo.
echo  A fatura sera salva na pasta: faturas\
echo =============================================
pause
