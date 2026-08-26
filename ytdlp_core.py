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
import hashlib
import threading
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

# ── Aynı anda indirme (paralel indirme) ayarları ─────────────────────────────
# Varsayılan ve ÖNERİLEN değer 1'dir (yani kapalı / sırayla indirme).
# YouTube aynı IP'den gelen çok sayıda eşzamanlı bağlantıyı hız sınırlama
# (HTTP 429) ile cezalandırabildiği için paralel indirme bilinçli olarak
# kullanıcı onayına bırakılmıştır; varsayılan davranışı DEĞİŞTİRMEZ.
PARALLEL_DOWNLOADS_DEFAULT = 1
PARALLEL_DOWNLOADS_MAX     = 5

# ── Günlük / geçmiş kayıtlarının en fazla tutulacağı satır sayısı ────────────
# Sınırsız büyümeyi önlemek için eski kayıtlar otomatik olarak budanır.
LOG_MAX_ENTRIES     = 1000
HISTORY_MAX_ENTRIES = 2000

# Windows'ta subprocess çağrılarında siyah konsol penceresinin açılmasını engeller
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ── yt-dlp.exe yönetimi (GitHub'dan indirme / güncelleme) ───────────────────
YTDLP_DIR = os.path.join(APPDATA_DIR, "bin")
Path(YTDLP_DIR).mkdir(parents=True, exist_ok=True)
YTDLP_EXE = os.path.join(YTDLP_DIR, "yt-dlp.exe")

# Bu adres her zaman yt-dlp/yt-dlp reposundaki EN GÜNCEL sürüme yönlendirir.
YTDLP_GITHUB_DOWNLOAD_URL   = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
# yt-dlp, her sürümle birlikte resmi SHA256 özet dosyasını da yayınlar.
# İndirilen .exe dosyasının bu dosyadaki değerle eşleştiğini doğrulayarak
# bozuk/eksik ya da araya girmiş (MITM) bir dosyayı çalıştırma riskini
# ortadan kaldırıyoruz.
YTDLP_GITHUB_CHECKSUMS_URL  = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS"


class YtdlpDogrulamaHatasi(Exception):
    """İndirilen yt-dlp.exe dosyası resmi özet (checksum) ile eşleşmediğinde fırlatılır."""
    pass


def ytdlp_exists() -> bool:
    """yt-dlp.exe dosyasının diskte var ve boş olmadığını kontrol eder."""
    return os.path.exists(YTDLP_EXE) and os.path.getsize(YTDLP_EXE) > 0


def _fetch_ytdlp_expected_sha256() -> str | None:
    """
    yt-dlp'nin resmi SHA2-256SUMS dosyasını indirir ve içinden "yt-dlp.exe"
    satırına ait beklenen özet değerini döndürür. Herhangi bir sebeple
    alınamazsa None döner (bu durumda çağıran taraf doğrulamayı atlayabilir
    ya da kullanıcıyı uyarabilir).
    """
    try:
        req = urllib.request.Request(
            YTDLP_GITHUB_CHECKSUMS_URL,
            headers={'User-Agent': random.choice(USER_AGENTS)}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode('utf-8', errors='ignore')
        for line in text.splitlines():
            line = line.strip()
            if line.lower().endswith('yt-dlp.exe') and len(line) >= 64:
                return line.split()[0].strip().lower()
    except Exception:
        pass
    return None


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download_ytdlp_exe(progress_cb=None, verify_checksum: bool = True) -> str:
    """
    GitHub'daki resmi yt-dlp/yt-dlp deposunun en güncel sürümünü indirir.
    Yarım kalan indirmelerin dosyayı bozmaması için önce .tmp'ye yazılır.

    progress_cb(pct, downloaded_bytes, total_bytes) verilirse her parça
    indirildikçe çağrılır. Sunucu dosya boyutunu bildirmezse pct=None gelir
    (belirsiz/pulse ilerleme çubuğu için).

    verify_checksum=True (varsayılan) olduğunda indirilen dosya, yt-dlp'nin
    yayınladığı resmi SHA256 özeti ile karşılaştırılır. Eşleşmezse dosya
    diskte YAZILMADAN silinir ve YtdlpDogrulamaHatasi fırlatılır — bozuk ya
    da güvenilmeyen bir .exe asla YTDLP_EXE konumuna taşınmaz. Özet dosyasına
    ağ sorunuyla ulaşılamazsa (ör. GitHub geçici olarak erişilemez) doğrulama
    atlanır ve dosya yine de kullanılabilir hale getirilir; bu durum
    progress_cb(None, -1, -1) özel çağrısıyla çağırana bildirilir.
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

    if verify_checksum:
        expected = _fetch_ytdlp_expected_sha256()
        if expected is None:
            # Özet dosyasına ulaşılamadı: kullanıcıya bildir, ama uygulamayı kilitleme.
            if progress_cb:
                progress_cb(None, -1, -1)
        else:
            actual = _sha256_of_file(tmp_path)
            if actual.lower() != expected:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise YtdlpDogrulamaHatasi(
                    "İndirilen dosyanın bütünlük denetimi (SHA256) başarısız oldu. "
                    "Dosya güvenlik nedeniyle silindi; lütfen tekrar deneyin."
                )

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


def get_ffmpeg_status_message() -> tuple[bool, str]:
    """Kurulum durumunu son kullanıcının anlayacağı, dosya adı/teknik ayrıntı
    içermeyen bir cümleyle döndürür: (kurulu_mu, mesaj)."""
    if get_ffmpeg_path():
        return True, "Video birleştirme bileşeni hazır — yüksek kaliteli indirmeler sorunsuz çalışacaktır."
    return False, ("Video birleştirme bileşeni eksik. Bazı yüksek kaliteli formatlar "
                    "(görüntü ve sesin ayrı indirilip birleştirilmesi gerekenler) çalışmayabilir. "
                    "Yardım menüsünden \"Eksik Bileşeni Tamamla\" seçeneğini kullanabilirsiniz.")


# ── Ayar / geçmiş / günlük dosyalarını güvenli okuma-yazma ──────────────────
import json as _json


def safe_load_json(path: str, default):
    """
    Bir JSON dosyasını okur. Dosya yoksa sessizce `default` döner (normal
    ilk-çalıştırma durumu). Dosya VARSA ama bozuksa/okunamıyorsa:
      - bozuk dosya, kaybolmasın diye ".bozuk-<zaman>" uzantısıyla yedeklenir,
      - (bozuldu_mu=True, default) döner ki çağıran taraf kullanıcıyı
        bilgilendirebilsin — veri sessizce yok edilmez.

    Dönüş: (bozuldu_mu: bool, veri)
    """
    if not os.path.exists(path):
        return False, default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return False, _json.load(f)
    except Exception:
        try:
            yedek = f"{path}.bozuk-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            os.replace(path, yedek)
        except OSError:
            pass
        return True, default


def safe_save_json(path: str, data) -> bool:
    """JSON verisini önce geçici dosyaya yazıp ardından yerine taşıyarak
    (atomik) kaydeder; yarım kalmış/bozuk dosya oluşma riskini azaltır."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def append_log_entry(entry: dict, max_entries: int = None) -> None:
    """Günlük dosyasına yeni bir kayıt ekler; dosyanın sınırsız büyümesini
    önlemek için en eski kayıtları budayarak en fazla `max_entries` (varsayılan
    LOG_MAX_ENTRIES) kayıt tutar."""
    if max_entries is None:
        max_entries = LOG_MAX_ENTRIES
    _, kayitlar = safe_load_json(LOG_FILE, [])
    if not isinstance(kayitlar, list):
        kayitlar = []
    kayitlar.append(entry)
    if len(kayitlar) > max_entries:
        kayitlar = kayitlar[-max_entries:]
    safe_save_json(LOG_FILE, kayitlar)


def is_valid_url(url: str) -> bool:
    return bool(re.match(
        r'https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/', url.strip()
    ))


# Windows'ta dosya/klasör adında kullanılamayan karakterler.
_INVALID_FILENAME_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
# yt-dlp çıktı şablonundaki "%(başlık)s" gibi alan adlarını (placeholder)
# koruyup geri kalan sabit metni denetlemek için kullanılır.
_TEMPLATE_FIELD_RE = re.compile(r'%\([^)]*\)[a-zA-Z0-9#]*')


def sanitize_filename_part(text: str) -> str:
    """Bir dosya/klasör adı parçasındaki Windows'ta geçersiz karakterleri
    alt çizgi ile değiştirir. Playlist başlığı gibi kullanıcı/İnternet
    kaynaklı serbest metinler için kullanılır."""
    if not text:
        return text
    cleaned = _INVALID_FILENAME_CHARS_RE.sub('_', text)
    return cleaned.strip().rstrip('.') or "adsiz"


def validate_output_template(template: str) -> tuple[bool, str]:
    """
    Kullanıcının Ayarlar sekmesinde girdiği çıktı adlandırma şablonunu
    denetler. yt-dlp'nin "%(başlık)s" gibi alan yer tutucularına dokunmadan,
    şablonun geri kalan SABİT kısmında:
      - üst klasöre çıkma girişimi (".." klasör adı olarak),
      - şablonun EN BAŞINDA sürücü harfi/kök yol (ör. "C:\\", "/etc/...")
        kullanımı,
      - Windows'ta dosya adında geçersiz karakter (: * ? " < > |)
    olup olmadığını kontrol eder. Alt klasör içeren meşru şablonlar
    (ör. "%(uploader)s/%(title)s.%(ext)s") GEÇERLİ kabul edilir.

    Dönüş: (gecerli_mi, hata_mesaji). Geçerliyse hata_mesaji boş string olur.
    """
    if not template or not template.strip():
        return False, "Çıktı adlandırma şablonu boş olamaz."

    # Şablonun EN BAŞI mutlak bir yol veya sürücü harfi olamaz. Bu kontrol
    # placeholder'lar çıkarılmadan ÖNCE, orijinal metnin başında yapılır;
    # aksi halde "%(uploader)s/%(title)s..." gibi meşru bir alt klasör
    # şablonu (placeholder kaldırılınca "/" ile başlar hale gelir) yanlışlıkla
    # reddedilir.
    if re.match(r'^[A-Za-z]:[\\/]', template) or template.startswith(('\\\\', '/', '\\')):
        return False, "Şablon, indirme klasörünün dışına çıkan bir sürücü/kök yol içeremez."

    # Yer tutucuları geçici olarak çıkar, kalan sabit metni denetle.
    sabit_kisim = _TEMPLATE_FIELD_RE.sub('', template)
    parcalar = re.split(r'[\\/]', sabit_kisim)

    for parca in parcalar:
        if parca == '..':
            return False, "Şablon üst klasöre çıkan (\"..\") bir klasör adı içeremez."
        if _INVALID_FILENAME_CHARS_RE.search(parca):
            return False, 'Şablon şu karakterleri içeremez:  \\ / : * ? " < > |'

    return True, ""


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


# Örnek geçerli değerler: "500K", "2M", "1.5M", "800k" (yt-dlp --limit-rate biçimi)
_SPEED_LIMIT_RE = re.compile(r'^\d+(\.\d+)?[KkMmGg]?$')


def validate_speed_limit(value: str) -> tuple[bool, str]:
    """Hız sınırı alanının yt-dlp'nin beklediği biçimde olup olmadığını
    kontrol eder (örn. 500K, 2M, 1.5M). Boş değer serbesttir (sınırsız)."""
    value = (value or "").strip()
    if value == "":
        return True, ""
    if _SPEED_LIMIT_RE.match(value):
        return True, ""
    return False, 'Geçersiz hız sınırı biçimi. Örnek: "500K" (kilobayt/sn) veya "2M" (megabayt/sn).'


def validate_proxy(value: str) -> tuple[bool, str]:
    """Proxy adresinin en azından "şema://sunucu:port" biçiminde olup
    olmadığını kabaca doğrular. Boş değer serbesttir (proxy kullanılmaz)."""
    value = (value or "").strip()
    if value == "":
        return True, ""
    if re.match(r'^(https?|socks[45]h?)://[^\s:/]+(:\d{1,5})?/?$', value, re.IGNORECASE):
        return True, ""
    return False, 'Geçersiz proxy adresi. Örnek: "http://sunucu:8080" veya "socks5://sunucu:1080".'


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
        self._lock = threading.Lock()

    def say(self, text: str, field: int = 0):
        with self._lock:
            if self._last.get(field) != text:
                self._last[field] = text
                self.sb.SetStatusText(text, field)


# ── İndirme ögesini temsil eden veri sınıfı ──────────────────────────────────
class DownloadItem:
    """
    Tek bir indirme kuyruğu ögesini temsil eder.

    NOT: `key` alanı, kuyruk/sözlük anahtarı olarak KULLANILMASI gereken
    benzersiz kimliktir — `url` DEĞİL. Aynı video adresi (ör. iki farklı
    playlist'te aynı video geçtiğinde) kuyrukta birden fazla kez oluşabilir;
    URL'yi anahtar olarak kullanmak bu durumda ögelerin birbirinin üzerine
    yazılmasına yol açardı. `idx`, ekranda gösterilen sıra numarasıdır ve
    tekil olması ZORUNLU değildir.
    """
    def __init__(self, url: str, idx: int):
        self.url             = url
        self.idx             = idx
        self.key              = f"{idx}:{id(self)}"
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
