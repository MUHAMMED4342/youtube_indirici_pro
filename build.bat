@echo off
REM ============================================================
REM  YouTube Indirici Pro - Otomatik Derleme Betigi
REM  Bu dosyayi youtube_indirici_pro.py / ytdlp_core.py /
REM  download_engine.py / youtube_indirici_pro.spec /
REM  youtube_indirici_pro.iss ile AYNI KLASORE koyup
REM  cift tiklayarak calistirin.
REM
REM  Yaptiklari:
REM   1) Python kurulu mu kontrol eder
REM   2) pyinstaller kurulu degilse otomatik kurar
REM   3) "pyinstaller youtube_indirici_pro.spec" ile ONEDIR build alir
REM   4) Inno Setup derleyicisini (ISCC.exe) bulur ve .iss dosyasini
REM      derleyerek kurulum .exe'sini uretir
REM   5) Sonucu acar / bilgilendirir
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   YouTube Indirici Pro - Otomatik Derleme
echo   Calisma klasoru: %cd%
echo ============================================================
echo.

REM ------------------------------------------------------------
REM 1) Python kontrolu
REM ------------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [HATA] Python bulunamadi. Lutfen once Python 3 kurun ve
    echo        kurulum sirasinda "Add python.exe to PATH" secenegini
    echo        isaretleyin: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python bulundu: %PYVER%
echo.

REM ------------------------------------------------------------
REM 2) Gerekli dosyalarin varligini kontrol et
REM ------------------------------------------------------------
if not exist "youtube_indirici_pro.spec" (
    echo [HATA] youtube_indirici_pro.spec bu klasorde bulunamadi.
    echo        Bu .bat dosyasini .spec ile ayni klasore koyun.
    pause
    exit /b 1
)
if not exist "youtube_indirici_pro.py" (
    echo [HATA] youtube_indirici_pro.py bu klasorde bulunamadi.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 3) pip / pyinstaller kontrolu ve kurulumu
REM ------------------------------------------------------------
echo [ADIM] Gerekli Python paketleri kontrol ediliyor (pyinstaller, wxPython)...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo   - pyinstaller bulunamadi, kuruluyor...
    python -m pip install --upgrade pip >nul
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [HATA] pyinstaller kurulamadi. Internet baglantinizi kontrol edin.
        pause
        exit /b 1
    )
) else (
    echo   - pyinstaller zaten kurulu.
)

python -c "import wx" 2>nul
if errorlevel 1 (
    echo   - wxPython bulunamadi, kuruluyor...
    python -m pip install wxPython
    if errorlevel 1 (
        echo [HATA] wxPython kurulamadi. Internet baglantinizi kontrol edin.
        echo        Manuel kurulum: pip install wxPython
        pause
        exit /b 1
    )
) else (
    echo   - wxPython zaten kurulu.
)
echo.

REM ------------------------------------------------------------
REM 4) Eski build/dist klasorlerini temizle (isteğe bagli ama onerilir)
REM ------------------------------------------------------------
echo [ADIM] Eski build/dist klasorleri temizleniyor...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
echo.

REM ------------------------------------------------------------
REM 5) PyInstaller ile ONEDIR build
REM ------------------------------------------------------------
echo [ADIM] PyInstaller ile derleme baslatiliyor (onedir modu)...
echo ------------------------------------------------------------
python -m PyInstaller youtube_indirici_pro.spec --noconfirm
echo ------------------------------------------------------------
if errorlevel 1 (
    echo [HATA] PyInstaller derlemesi basarisiz oldu. Yukaridaki hatayi inceleyin.
    pause
    exit /b 1
)
if not exist "dist\YouTubeIndiriciPro\YouTubeIndiriciPro.exe" (
    echo [HATA] Beklenen cikti bulunamadi: dist\YouTubeIndiriciPro\YouTubeIndiriciPro.exe
    pause
    exit /b 1
)
echo [OK] PyInstaller derlemesi tamamlandi: dist\YouTubeIndiriciPro\
echo.

REM ------------------------------------------------------------
REM 6) Inno Setup derleyicisini (ISCC.exe) bul
REM ------------------------------------------------------------
REM NOT: Bu bolum bilerek COK SATIRLI "if ( ... )" bloklari KULLANMAZ.
REM Sebebi: "%ProgramFiles(x86)%" degiskeninin adindaki ")" karakteri,
REM cok satirli bir blok icinde kullanildiginda cmd.exe'nin blok
REM parantezini erken kapatmasina ve "was unexpected at this time"
REM hatasina yol acabiliyor. Bunun yerine tek satirlik komutlar ve
REM goto ile ilerliyoruz; bu yapi bu soruna karsi tamamen guvenlidir.

set "ISCC="
set "PFX86=%ProgramFiles(x86)%"
echo [ADIM] Inno Setup (ISCC.exe) araniyor...

where ISCC.exe >nul 2>nul
if errorlevel 1 goto :iscc_try_pf6x
for /f "delims=" %%p in ('where ISCC.exe') do set "ISCC=%%p"
echo   - PATH icinde bulundu.
goto :iscc_found

:iscc_try_pf6x
if not exist "%PFX86%\Inno Setup 6\ISCC.exe" goto :iscc_try_pf6
set "ISCC=%PFX86%\Inno Setup 6\ISCC.exe"
goto :iscc_found

:iscc_try_pf6
if not exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" goto :iscc_try_pf5x
set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
goto :iscc_found

:iscc_try_pf5x
if not exist "%PFX86%\Inno Setup 5\ISCC.exe" goto :iscc_try_local7
set "ISCC=%PFX86%\Inno Setup 5\ISCC.exe"
goto :iscc_found

:iscc_try_local7
if not exist "%LocalAppData%\Programs\Inno Setup 7\ISCC.exe" goto :iscc_try_local6
set "ISCC=%LocalAppData%\Programs\Inno Setup 7\ISCC.exe"
goto :iscc_found

:iscc_try_local6
if not exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" goto :iscc_try_deep_local
set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
goto :iscc_found

:iscc_try_deep_local
echo   - Sabit yollarda bulunamadi, AppData\Local\Programs altinda derin arama yapiliyor...
for /f "delims=" %%f in ('dir /b /s "%LocalAppData%\Programs\ISCC.exe" 2^>nul') do set "ISCC=%%f" & goto :iscc_found
goto :iscc_try_deep_pfx86

:iscc_try_deep_pfx86
echo   - Program Files x86 altinda derin arama yapiliyor...
for /f "delims=" %%f in ('dir /b /s "%PFX86%\ISCC.exe" 2^>nul') do set "ISCC=%%f" & goto :iscc_found
goto :iscc_try_deep_pf

:iscc_try_deep_pf
echo   - Program Files altinda derin arama yapiliyor...
for /f "delims=" %%f in ('dir /b /s "%ProgramFiles%\ISCC.exe" 2^>nul') do set "ISCC=%%f" & goto :iscc_found
goto :iscc_not_found

:iscc_not_found
echo [UYARI] Inno Setup derleyicisi (ISCC.exe) bulunamadi.
echo         Not: ISIDE.exe Inno Setup'in gorsel editorudur, ISCC.exe
echo         ise komut satiri derleyicisidir; bu betik ISCC.exe arar
echo         ve genelde ISIDE.exe ile AYNI klasordedir.
echo.
echo         Kurulum .exe'si otomatik olusturulamadi, ancak program
echo         dosyalari hazir: dist\YouTubeIndiriciPro\
echo.
echo         Cozum 1: youtube_indirici_pro.iss dosyasina cift tiklayip
echo                  Inno Setup IDE'de acin, F9 (Compile) ile derleyin.
echo         Cozum 2: ISCC.exe'nin gercek yolunu bu .bat dosyasinin en
echo                  ustune, "setlocal" satirindan hemen sonra asagidaki
echo                  gibi elle ekleyin:
echo                  set "ISCC=C:\tam\yol\ISCC.exe"
echo.
pause
exit /b 0

:iscc_found
echo [OK] Inno Setup bulundu: %ISCC%
echo.

if not exist "youtube_indirici_pro.iss" (
    echo [UYARI] youtube_indirici_pro.iss bu klasorde bulunamadi.
    echo         Kurulum .exe'si olusturulamiyor, ancak program dosyalari
    echo         hazir: dist\YouTubeIndiriciPro\
    pause
    exit /b 0
)

REM ------------------------------------------------------------
REM 7) Inno Setup ile kurulum .exe'si olustur
REM ------------------------------------------------------------
echo [ADIM] Inno Setup ile kurulum paketi olusturuluyor...
echo ------------------------------------------------------------
"%ISCC%" "youtube_indirici_pro.iss"
echo ------------------------------------------------------------
if errorlevel 1 (
    echo [HATA] Inno Setup derlemesi basarisiz oldu. Yukaridaki hatayi inceleyin.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   TAMAMLANDI!
echo   - Program dosyalari : dist\YouTubeIndiriciPro\
echo   - Kurulum dosyasi    : youtube_indirici_pro.setup\youtube_indirici_pro.setup.exe
echo ============================================================
echo.

REM Kurulum dosyasinin bulundugu klasoru ac
if exist "youtube_indirici_pro.setup" (
    explorer "youtube_indirici_pro.setup"
)

pause
exit /b 0
