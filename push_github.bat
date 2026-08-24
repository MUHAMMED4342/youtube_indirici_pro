@echo off
REM ============================================================
REM  YouTube Indirici Pro - GitHub'a Otomatik Push + Release Betigi
REM  Bu dosyayi git reposunun KOK klasorune (repo icinde ".git"
REM  klasorunun bulundugu yere) koyup cift tiklayarak calistirin.
REM
REM  Yaptiklari:
REM   1) git kurulu mu ve bu klasor bir git reposu mu kontrol eder
REM   2) Degisiklikleri "git add -A" ile isaretler
REM   3) Commit mesaji sorar (bos birakilirsa otomatik tarih/saat verir)
REM   4) "git push" ile GitHub'a gonderir
REM   5) Eger GitHub CLI (gh) kuruluysa ve dist\..\youtube_indirici_pro.setup
REM      klasorunde .exe varsa, YENI bir GitHub Release olusturup bu
REM      .exe'yi otomatik asset olarak yukler (releases/latest/download/
REM      linkinizin calismasi icin gereken adim budur; sade "git push"
REM      Release olusturmaz, cunku buyuk .exe dosyalari repoya değil
REM      Release'e yuklenir).
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   YouTube Indirici Pro - GitHub Push
echo   Calisma klasoru: %cd%
echo ============================================================
echo.

REM ------------------------------------------------------------
REM 1) git kurulu mu kontrol et
REM ------------------------------------------------------------
where git >nul 2>nul
if errorlevel 1 (
    echo [HATA] git bulunamadi. Lutfen once Git'i kurun:
    echo        https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 2) Bu klasor bir git reposu mu kontrol et
REM ------------------------------------------------------------
git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
    echo [HATA] Bu klasor bir git reposu degil.
    echo        Bu .bat dosyasini reponun KOK klasorune
    echo        ".git" klasorunun yaninda oldugundan emin olun.
    echo        Eger repo daha once klonlanmadiysa once su komutu calistirin:
    echo        git clone https://github.com/MUHAMMED4342/youtube_indirici_pro.git
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 3) Degisiklik var mi kontrol et
REM ------------------------------------------------------------
git status --porcelain > "%TEMP%\gitstatus.txt"
for %%A in ("%TEMP%\gitstatus.txt") do set GITSTATUS_SIZE=%%~zA
if "%GITSTATUS_SIZE%"=="0" (
    echo [BILGI] Yerel degisiklik bulunamadi, commit atilacak bir sey yok.
    del "%TEMP%\gitstatus.txt" >nul 2>nul
    goto :ask_release
)
del "%TEMP%\gitstatus.txt" >nul 2>nul

echo [ADIM] Degisiklikler goruntuleniyor:
echo ------------------------------------------------------------
git status -s
echo ------------------------------------------------------------
echo.

REM ------------------------------------------------------------
REM 4) Tum degisiklikleri isaretle
REM ------------------------------------------------------------
git add -A

REM ------------------------------------------------------------
REM 5) Commit mesaji sor
REM ------------------------------------------------------------
set "COMMIT_MSG="
set /p COMMIT_MSG="Commit mesaji girin (bos birakip Enter'a basarsaniz otomatik tarih/saat kullanilir): "
if "%COMMIT_MSG%"=="" (
    for /f "tokens=1-3 delims=/. " %%a in ("%date%") do set DTAG=%%a-%%b-%%c
    set "COMMIT_MSG=Otomatik guncelleme - !DTAG! !time!"
)

git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
    echo [HATA] Commit basarisiz oldu. Yukaridaki hatayi inceleyin.
    pause
    exit /b 1
)
echo [OK] Commit olusturuldu.
echo.

REM ------------------------------------------------------------
REM 6) GitHub'a gonder
REM ------------------------------------------------------------
echo [ADIM] GitHub'a gonderiliyor (git push)...
echo ------------------------------------------------------------
git push
echo ------------------------------------------------------------
if errorlevel 1 (
    echo [HATA] Push basarisiz oldu. Internet baglantinizi ve GitHub
    echo        giris bilgilerinizi (kimlik dogrulama) kontrol edin.
    echo        Ilk kez push ediyorsaniz asagidaki komutla remote'u ve
    echo        upstream'i ayarlamaniz gerekebilir:
    echo        git push -u origin main
    pause
    exit /b 1
)
echo [OK] Push tamamlandi.
echo.

:ask_release
REM ------------------------------------------------------------
REM 7) Kurulum .exe'sini GitHub Release'e yukle (opsiyonel)
REM ------------------------------------------------------------
REM Onemli: buyuk .exe dosyalari normal "git push" ile degil, GitHub
REM Release "asset" olarak yuklenmelidir. Bunun icin GitHub CLI (gh)
REM gereklidir: https://cli.github.com/
where gh >nul 2>nul
if errorlevel 1 (
    echo [BILGI] GitHub CLI (gh) bulunamadi, Release/asset yukleme adimi atlandi.
    echo         Kurulum .exe'nizi (youtube_indirici_pro.setup.exe^) GitHub
    echo         sitesinden "Releases -^> Draft a new release" ile elle
    echo         yukleyebilir, ya da GitHub CLI kurup bu betigi tekrar
    echo         calistirarak bu adimi da otomatiklestirebilirsiniz:
    echo         https://cli.github.com/
    echo.
    goto :the_end
)

if not exist "youtube_indirici_pro.setup\youtube_indirici_pro.setup.exe" (
    echo [BILGI] youtube_indirici_pro.setup\youtube_indirici_pro.setup.exe
    echo         bulunamadi, Release olusturma adimi atlaniyor.
    echo         Once build.bat ile kurulum dosyasini olusturun.
    echo.
    goto :the_end
)

set "MAKE_RELEASE="
set /p MAKE_RELEASE="GitHub Release olusturup youtube_indirici_pro.setup.exe'yi yuklemek ister misiniz? (E/H): "
if /i not "%MAKE_RELEASE%"=="E" goto :the_end

set "REL_TAG="
set /p REL_TAG="Release/tag adi girin (ornek: v2.0.1): "
if "%REL_TAG%"=="" (
    echo [HATA] Tag adi bos olamaz, Release olusturma iptal edildi.
    goto :the_end
)

echo [ADIM] GitHub Release olusturuluyor ve .exe yukleniyor...
echo ------------------------------------------------------------
gh release create "%REL_TAG%" "youtube_indirici_pro.setup\youtube_indirici_pro.setup.exe" --title "%REL_TAG%" --generate-notes
echo ------------------------------------------------------------
if errorlevel 1 (
    echo [HATA] Release olusturma basarisiz oldu. Yukaridaki hatayi inceleyin.
    echo        "gh auth login" ile giris yaptiginizdan emin olun.
    pause
    exit /b 1
)
echo [OK] Release olusturuldu ve youtube_indirici_pro.setup.exe yuklendi.
echo        releases/latest/download/youtube_indirici_pro.setup.exe
echo        linki artik bu surumu gosterecektir.
echo.

:the_end
echo ============================================================
echo   TAMAMLANDI!
echo ============================================================
pause
exit /b 0
