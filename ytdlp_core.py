"""
YouTube İndirici Pro — Çekirdek Modül
Görme Engelli Erişilebilirlik (NVDA / JAWS / Narrator) ile tam uyumlu.

Bu modül; ayar/geçmiş dosya yolları, yt-dlp.exe ve ffmpeg yönetimi, URL
doğrulama, klavye kısayolları sözlüğü, durum çubuğu okuyucusu (StatusSpeaker),
indirme ögesi veri sınıfı (DownloadItem) ve indirme sonrası kalan geçici
dosyaları (.part, .ytdl, birleştirilmemiş .webm/.m4a parça dosyaları vb.)
temizleyen yardımcı fonksiyonları içerir.

Diğer modüller (download_engine.py, youtube_indirici_pro.py) bu dosyadan
içe aktarma yapar.
"""

import wx
import os
import sys
import re
import random
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ── Uygulama dizini ──────────────────────────────────────────────────────────
APPDATA_DIR = os.path.join(os.environ.get('APPDATA', os.getcwd()), 'YouTubeIndiriciPro')
Path(APPDATA_DIR).mkdir(parents=True, exist_ok=True)

SETTINGS_FILE   = os.path.join(APPDATA_DIR, "ayarlar.json")
HISTORY_FILE    = os.path.join(APPDATA_DIR, "gecmis.json")
LOG_FILE        = os.path.join(APPDATA_DIR, "islem_gunlugu.json")
DOWNLOADED_URLS_FILE = os.path.join(APPDATA_DIR, "indirilen_adresler.json")

# ── Klavye kısayolları sözlüğü ───────────────────────────────────────────────
KISAYOLLAR = {
    "Ctrl+I":     "İndirmeyi Başlat",
    "Ctrl+Q":     "İndirmeyi İptal Et",
    "Ctrl+R":     "Başarısızları Yeniden Dene",
    "Ctrl+V":     "Panoya Yapıştır (URL alanında)",
    "Ctrl+S":     "Ayarları Kaydet",
    "Ctrl+E":     "Günlüğü Dışa Aktar",
    "Ctrl+O":     "Dosyadan URL İçe Aktar (.txt)",
    "Delete":     "Kuyruktan Seçili Ögeyi Sil",
    "F1":         "Yardım / Kısayolları Göster",
    "F5":         "İndirme Durumunu Sesli Oku",
    "F6":         "Başarısız Ögeleri Yeniden Dene",
    "Tab":        "Sonraki ögeye geç",
    "Shift+Tab":  "Önceki ögeye geç",
    "Alt+1":      "İndirme Merkezi sekmesi",
    "Alt+2":      "Ayarlar sekmesi",
    "Alt+3":      "Geçmiş sekmesi",
    "Alt+4":      "Günlük sekmesi",
}

# ── User-Agent havuzu (429'a karşı rotasyon) ─────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ── Retry ayarları ───────────────────────────────────────────────────────────
RETRY_MAX          = 4        # Maksimum deneme sayısı
RETRY_BASE_SLEEP   = 8        # İlk bekleme süresi (saniye) — her denemede 2x artar
RETRY_JITTER_MAX   = 5        # Rasgele ek bekleme üst sınırı (saniye)
BETWEEN_DL_MIN     = 3        # İndirmeler arası minimum bekleme
BETWEEN_DL_MAX     = 8        # İndirmeler arası maksimum bekleme

# Windows'ta subprocess çağrılarında siyah konsol penceresinin açılmasını engeller
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ── yt-dlp.exe yönetimi (GitHub'dan indirme / güncelleme) ───────────────────
YTDLP_DIR = os.path.join(APPDATA_DIR, "bin")
Path(YTDLP_DIR).mkdir(parents=True, exist_ok=True)
YTDLP_EXE = os.path.join(YTDLP_DIR, "yt-dlp.exe")

# Bu adres her zaman yt-dlp/yt-dlp reposundaki EN GÜNCEL sürüme yönlendirir.
YTDLP_GITHUB_DOWNLOAD_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def ytdlp_exists() -> bool:
    """yt-dlp.exe dosyasının diskte var ve boş olmadığını kontrol eder."""
    return os.path.exists(YTDLP_EXE) and os.path.getsize(YTDLP_EXE) > 0


def download_ytdlp_exe(progress_cb=None) -> str:
    """
    GitHub'daki resmi yt-dlp/yt-dlp deposunun en güncel sürümünü indirir.
    Yarım kalan indirmelerin dosyayı bozmaması için önce .tmp'ye yazılır.

    progress_cb(pct, downloaded_bytes, total_bytes) verilirse her parça
    indirildikçe çağrılır. Sunucu dosya boyutunu bildirmezse pct=None gelir
    (belirsiz/pulse ilerleme çubuğu için).
    """
    tmp_path = YTDLP_EXE + ".tmp"
    req = urllib.request.Request(
        YTDLP_GITHUB_DOWNLOAD_URL,
        headers={'User-Agent': random.choice(USER_AGENTS)}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        total_header = resp.getheader('Content-Length')
        total = int(total_header) if total_header else None
        downloaded = 0
        with open(tmp_path, 'wb') as out:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    pct = int(downloaded / total * 100) if total else None
                    progress_cb(pct, downloaded, total)
    os.replace(tmp_path, YTDLP_EXE)
    return YTDLP_EXE


def get_ytdlp_version():
    """Yüklü yt-dlp.exe sürümünü döndürür, yoksa None."""
    if not ytdlp_exists():
        return None
    try:
        proc = subprocess.run(
            [YTDLP_EXE, '--version'],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW)
        return proc.stdout.strip() or None
    except Exception:
        return None


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────
def get_ffmpeg_path():
    base = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    ffmpeg  = os.path.join(base, "ffmpeg.exe")
    ffprobe = os.path.join(base, "ffprobe.exe")
    if os.path.exists(ffmpeg) and os.path.exists(ffprobe):
        return base
    return None


def is_valid_url(url: str) -> bool:
    return bool(re.match(
        r'https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/', url.strip()
    ))


def normalize_urls(raw: str) -> list:
    """Metin bloğundan URL'leri ayıklar; boşluk, virgül ve satır sonu ayraç olarak kabul edilir."""
    parts = re.split(r'[\n\r,\s]+', raw.strip())
    seen = set()
    result = []
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            result.append(p)
    return result


def bildir(parent, mesaj: str, baslik: str = "Bilgi", icon=wx.ICON_INFORMATION):
    dlg = wx.MessageDialog(parent, mesaj, baslik, wx.OK | icon)
    dlg.ShowModal()
    dlg.Destroy()


# ── Geçici / yarım kalmış dosyaları temizleme ────────────────────────────────
# yt-dlp normalde bir indirme başarıyla bitip parçalar (video+ses) ffmpeg ile
# birleştirildikten sonra geçici parça dosyalarını (.part, .fNNN.ext, .ytdl)
# kendisi siler. Ancak indirme İPTAL EDİLİRSE ya da işlem tüm denemelere
# rağmen KALICI OLARAK BAŞARISIZ olursa, bu geçici dosyalar diskte kalabilir
# (ör. "Video.f137.mp4", "Video.f251.webm", "Video.mp4.part", "Video.ytdl").
# Aşağıdaki fonksiyonlar bu durumu düzeltir.

# yt-dlp'nin ".part" indirmesinden önce oluşturduğu parça (fragment) dosya
# adları "...f137.mp4", "...f251.webm" gibi biter. Birleştirme TAMAMLANDIĞINDA
# ortaya çıkan asıl dosya adında bu "fNNN." kalıbı ASLA bulunmaz; bu yüzden
# bu kalıp geçici dosyaları güvenle ayırt etmek için kullanılabilir.
_FRAGMENT_RE = re.compile(r'\.f\d{2,4}\.[A-Za-z0-9]{2,5}$')


def _is_leftover_temp_file(filename: str) -> bool:
    """Dosya adının yt-dlp'ye ait yarım kalmış bir geçici/parça dosyası
    olup olmadığını denetler (.part, .ytdl, .part-Frag..., ...fNNN.ext)."""
    return (
        filename.endswith('.part')
        or filename.endswith('.ytdl')
        or '.part-Frag' in filename
        or bool(_FRAGMENT_RE.search(filename))
    )


def cleanup_temp_files(directory: str) -> list:
    """
    Verilen klasördeki yt-dlp'ye ait yarım kalmış geçici/parça dosyalarını
    (.part, .ytdl, .part-FragNN.part, birleştirilmemiş .webm/.m4a/.mp4 parça
    dosyaları vb.) siler. İndirmeler sıralı (tek tek) yapıldığı için bu
    temizlik yalnızca iptal edilen veya kalıcı olarak başarısız olan indirme
    için diskte kalan dosyaları hedefler; başka bir indirmeyle çakışma riski
    yoktur.

    Silinen dosya yollarının listesini döndürür (loglama için kullanılabilir).
    """
    removed = []
    if not directory or not os.path.isdir(directory):
        return removed
    try:
        for name in os.listdir(directory):
            if not _is_leftover_temp_file(name):
                continue
            full = os.path.join(directory, name)
            if not os.path.isfile(full):
                continue
            try:
                os.remove(full)
                removed.append(full)
            except OSError:
                pass  # Dosya hâlâ kilitliyse sessizce geç, uygulamayı bozma
    except OSError:
        pass
    return removed


def terminate_process_tree(proc: subprocess.Popen) -> None:
    """
    Bir yt-dlp.exe sürecini VE onun başlattığı alt süreçleri (özellikle
    ffmpeg.exe birleştirme/dönüştürme sırasında) birlikte sonlandırır.

    Sadece proc.terminate() çağırmak yalnızca yt-dlp.exe'yi kapatır; onun
    başlattığı ffmpeg.exe alt süreci arka planda çalışmaya devam edebilir ve
    çıktı dosyasını kilitli tutabilir. Bu da iptal sonrası ".part"/".webm"
    gibi geçici dosyaların silinememesinin başlıca sebebiydi. Windows'ta
    "taskkill /T" tüm alt süreç ağacını birlikte kapatarak bunu önler.
    """
    try:
        if os.name == 'nt':
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                capture_output=True, timeout=10,
                creationflags=CREATE_NO_WINDOW)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ── StatusSpeaker: Durum çubuğu + NVDA dostu metin güncelleyici ──────────────
class StatusSpeaker:
    def __init__(self, statusbar):
        self.sb = statusbar
        self._last = {}

    def say(self, text: str, field: int = 0):
        if self._last.get(field) != text:
            self._last[field] = text
            self.sb.SetStatusText(text, field)


# ── İndirme ögesini temsil eden veri sınıfı ──────────────────────────────────
class DownloadItem:
    def __init__(self, url: str, idx: int):
        self.url             = url
        self.idx             = idx
        self.title           = url
        self.status          = "Kuyrukta"
        self.progress        = "0%"
        self.speed           = ""
        self.eta             = ""
        self.error           = ""
        self.start_time      = datetime.now().isoformat()
        self.end_time        = None
        # Playlist alanları (yoksa None)
        self.playlist_title  = None
        self.playlist_idx    = None
        self.playlist_total  = None
