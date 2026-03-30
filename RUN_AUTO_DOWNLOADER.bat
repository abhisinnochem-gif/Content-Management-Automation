@echo off
echo ============================================
echo   Auto Image Downloader
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org
    pause
    exit /b
)

REM Install required packages
echo Installing required packages...
pip install requests openpyxl beautifulsoup4 pillow numpy rembg onnxruntime -q
pip install "crawlee[playwright]" -q
playwright install chromium 2>nul
echo Packages ready.
echo.

REM Check Excel file exists
set EXCEL=Product_Image_Resize_.xlsx
if not exist "%EXCEL%" (
    echo ERROR: "%EXCEL%" not found in this folder.
    echo Place the Excel file next to this .bat file and try again.
    pause
    exit /b
)

REM Run the auto downloader
echo Starting auto download...
echo.
python auto_download.py --input "%EXCEL%"

echo.
echo ============================================
echo   Done! Check the resized_images folder.
echo ============================================
pause
