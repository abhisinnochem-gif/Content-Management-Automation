@echo off
set ORT_LOGGING_LEVEL=3
set CUDA_VISIBLE_DEVICES=-1
set ORT_DISABLE_CUDA=1
set ONNXRUNTIME_PROVIDERS=CPUExecutionProvider
cd /d C:\Users\ramchander\Downloads\Auto_Downloader

echo =======================================
echo   Auto Image Downloader
echo =======================================
echo.

REM ── SINGLE PRODUCT MODE ──
REM Uncomment and edit the line below to run a single product:
REM python auto_download.py --url https://synevit.com --upc 860011318019 --ptid 223140 --name "Neurocomplex-B Slow-Release" --vendor "Synevit"

REM ── EXCEL BATCH MODE (default) ──
python auto_download.py --input "Product_Image_Resize_.xlsx"

echo.
echo =======================================
echo   DONE — Press any key to close
echo =======================================
pause
