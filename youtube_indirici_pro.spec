# -*- mode: python ; coding: utf-8 -*-
#
# Kullanım:
#   pyinstaller youtube_indirici_pro.spec
#
# Bu spec dosyası youtube_indirici_pro.py, ytdlp_core.py ve
# download_engine.py dosyalarının hepsini ÇOK DOSYALI (onedir) modda
# paketler. Tek bir .exe yerine "dist\YouTubeIndiriciPro\" klasörü
# oluşur; klasörün içinde YouTubeIndiriciPro.exe ve tüm bağımlılıklar
# (DLL'ler, wx kütüphaneleri, ffmpeg.exe/ffprobe.exe vb.) ayrı ayrı yer
# alır. Diğer iki .py dosyasını ayrıca eklemenize gerek yoktur; normal
# Python import'ları olduğu için PyInstaller bunları otomatik bulur —
# yeter ki üçü de aynı klasörde olsun.
#
# onedir modunun onefile'a göre avantajları:
#   - Açılış hızı çok daha yüksektir (exe her çalıştığında geçici bir
#     klasöre kendini açmaz).
#   - Antivirüs yazılımlarının yanlış pozitif (false positive) verme
#     olasılığı belirgin şekilde daha düşüktür.
#   - Inno Setup ile paketlemek için daha uygundur (aşağıdaki .iss
#     dosyasına bakınız).
#
# ffmpeg.exe / ffprobe.exe bu .spec dosyasıyla AYNI KLASÖRDE bulunursa
# otomatik olarak çıktı klasörüne kopyalanır (program çalışırken
# sys._MEIPASS yerine exe ile aynı klasörde bulunur — onedir modunda
# _MEIPASS, dist\YouTubeIndiriciPro\ klasörünün kendisidir). Yoksa
# program yalnızca yt-dlp.exe ile format birleştirmeden (remux/merge)
# çalışabilir; ses dönüştürme ve altyazı gömme gibi ffmpeg gerektiren
# özellikler devre dışı kalır.

import os

block_cipher = None

_ffmpeg_datas = []
for _fname in ("ffmpeg.exe", "ffprobe.exe"):
    if os.path.exists(_fname):
        _ffmpeg_datas.append((_fname, "."))

# İkon dosyası varsa (isteğe bağlı) exe'ye ve Inno Setup kısayollarına
# gömülür. Yoksa varsayılan PyInstaller ikonu kullanılır.
_icon_path = "icon.ico" if os.path.exists("icon.ico") else None

a = Analysis(
    ['youtube_indirici_pro.py'],
    pathex=[os.path.abspath('.')],
    binaries=[],
    datas=_ffmpeg_datas,
    hiddenimports=['wx.adv'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── ONEDIR MODU ──────────────────────────────────────────────────────
# EXE() içine artık a.binaries / a.zipfiles / a.datas VERİLMEZ; bunlar
# yerine COLLECT() adımına aktarılır. Bu, PyInstaller'a "tek dosyaya
# sıkıştırma, klasöre topla" der.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir modunun anahtarı: binary'leri EXE'ye gömme
    name='YouTubeIndiriciPro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # Konsol penceresi açılmaz (GUI uygulaması)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_path,         # icon.ico varsa exe'ye gömülür
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='YouTubeIndiriciPro',   # -> dist\YouTubeIndiriciPro\ klasörü
)
