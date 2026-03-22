@echo off
echo ============================================
echo   Product Image WebP Downloader
echo ============================================
echo.

REM Check if Python is installed
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

REM Run the downloader
echo Starting image download...
echo.
python download_webp.py --input "Product_Image_Resize_.xlsx"

echo.
echo ============================================
echo   Done! Check the resized_images folder.
echo ============================================
pause
