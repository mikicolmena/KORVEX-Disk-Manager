@echo off
REM build_windows.bat - Genera dist\KORVEX-Disk-Manager.exe
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [X] No se encontro Python. Instalalo desde https://www.python.org/ ^(marca "Add to PATH"^).
    exit /b 1
)

for %%F in (KorvexLogo.ico KorvexLogo-256.png KorvexLogo-128.png) do (
    if not exist "%%F" (
        echo [X] Falta %%F
        exit /b 1
    )
)

echo [*] Instalando dependencias...
python -m pip install --upgrade -r requirements.txt pyinstaller || exit /b 1

echo [*] Compilando...
python -m PyInstaller --noconfirm --clean korvex-disk-manager.spec || exit /b 1

echo.
echo [OK] Listo: dist\KORVEX-Disk-Manager.exe
endlocal
