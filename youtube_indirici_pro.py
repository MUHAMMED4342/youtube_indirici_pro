"""
YouTube İndirici Pro — v3.0
Görme Engelli Erişilebilirlik (NVDA / JAWS / Narrator) ile tam uyumlu.

Yeni (v3.0) özellikler:
  • Altyazı indirme (otomatik/manuel, gömme veya ayrı .srt)
  • SponsorBlock — sponsor/tanıtım bölümlerini otomatik atlama
  • Proxy desteği
  • Özelleştirilebilir dosya adı şablonu
  • 4K (2160p) / 1440p ve Opus/AAC ses biçimleri
  • .txt dosyasından toplu URL içe aktarma
  • Kuyruktan öge silme / tamamlananları temizleme
  • Sistem tepsisine küçültme (isteğe bağlı)
  • Video/oynatma listesi önizleme (süre, kanal, tarih) arama sonuçlarında
"""

APP_VERSION = "3.0"

import wx
try:
    import wx.adv
    HAS_WX_ADV = True
except Exception:
    HAS_WX_ADV = False
import threading
import queue
import os
import sys
import json
import time
import re
import random
import logging
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


# ── StatusSpeaker: Durum çubuğu + NVDA dostu metin güncelleyici ──────────────
class StatusSpeaker:
    def __init__(self, statusbar):
        self.sb = statusbar
        self._last = {}

    def say(self, text: str, field: int = 0):
        if self._last.get(field) != text:
            self._last[field] = text
            self.sb.SetStatusText(text, field)


# ── Klasik indirme penceresi: erişilebilir ilerleme çubuğu ──────────────────
class YtdlpProgressDialog(wx.Dialog):
    """
    yt-dlp motoru indirilirken/güncellenirken gösterilen küçük, klasik
    tarzda indirme penceresi. Ekran okuyucular için:
    - Gauge ve etiket odakta tutulur; SetLabel çağrıları odaklı kontrolde
      isim değişikliği olayı tetikler, bu sayede NVDA/JAWS otomatik okur.
    - Kapatma (X) düğmesi devam eden indirmeyi iptal etmez, sadece gizler.
    """
    def __init__(self, parent, title="İndiriliyor..."):
        super().__init__(
            parent, title=title, size=(430, 190),
            style=wx.CAPTION | wx.SYSTEM_MENU)
        panel = wx.Panel(self)
        panel.SetName("İndirme penceresi paneli")
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.lbl_message = wx.StaticText(
            panel, label="Hazırlanıyor...", name="İndirme durumu")
        self.lbl_message.SetToolTip("Anlık indirme/güncelleme durumu.")
        sizer.Add(self.lbl_message, 0, wx.ALL | wx.EXPAND, 14)

        self.gauge = wx.Gauge(
            panel, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH,
            name="İndirme ilerlemesi")
        self.gauge.SetToolTip(
            "İndirme yüzdesi. Boyut bilinmiyorsa çubuk sürekli hareket eder.")
        sizer.Add(self.gauge, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 14)

        self.lbl_percent = wx.StaticText(panel, label="%0", name="Yüzde etiketi")
        sizer.Add(self.lbl_percent, 0, wx.ALL, 14)

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizerAndFit(outer)
        self.CenterOnParent()

        self._pulse_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_pulse, self._pulse_timer)
        self.Bind(wx.EVT_CLOSE, lambda e: self.Hide())

        self.lbl_message.SetFocus()  # NVDA odaklanınca ilerlemeyi otomatik okur

    def _on_pulse(self, event):
        self.gauge.Pulse()

    def set_progress(self, pct, message: str = None):
        """pct: 0-100 arası tam sayı, veya boyut bilinmiyorsa None (pulse modu)."""
        if pct is None:
            if not self._pulse_timer.IsRunning():
                self._pulse_timer.Start(100)
            percent_text = "İşleniyor..."
        else:
            if self._pulse_timer.IsRunning():
                self._pulse_timer.Stop()
            pct = max(0, min(100, pct))
            self.gauge.SetValue(pct)
            percent_text = f"%{pct}"

        if self.lbl_percent.GetLabel() != percent_text:
            self.lbl_percent.SetLabel(percent_text)
        if message and self.lbl_message.GetLabel() != message:
            self.lbl_message.SetLabel(message)

    def close_dialog(self):
        if self._pulse_timer.IsRunning():
            self._pulse_timer.Stop()
        self.Hide()
        self.Destroy()


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


# ── Sistem tepsisi simgesi (isteğe bağlı) ────────────────────────────────────
if HAS_WX_ADV:
    class AppTrayIcon(wx.adv.TaskBarIcon):
        def __init__(self, frame):
            super().__init__()
            self.frame = frame
            icon = wx.Icon()
            bmp = wx.ArtProvider.GetBitmap(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16))
            icon.CopyFromBitmap(bmp)
            self.SetIcon(icon, "YouTube İndirici Pro")
            self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self._on_restore)

        def CreatePopupMenu(self):
            menu = wx.Menu()
            item_restore = menu.Append(wx.ID_ANY, "Pencereyi Göster")
            menu.AppendSeparator()
            item_exit = menu.Append(wx.ID_ANY, "Çıkış")
            self.Bind(wx.EVT_MENU, self._on_restore, item_restore)
            self.Bind(wx.EVT_MENU, self._on_exit, item_exit)
            return menu

        def _on_restore(self, event):
            self.frame.Show()
            self.frame.Iconize(False)
            self.frame.Raise()

        def _on_exit(self, event):
            self.frame.Close(force=True)


# ── Ana uygulama penceresi ────────────────────────────────────────────────────
class YouTubeDownloader(wx.Frame):

    def __init__(self):
        super().__init__(
            parent=None,
            title=f"YouTube İndirici Pro  v{APP_VERSION}",
            size=(740, 680),
        )

        self.settings = self._load(SETTINGS_FILE, {
            'dir': os.path.expanduser('~\\Downloads'),
            'playlist': False,
            'embed_meta': True,
            'speed_limit': '',
            'notifications': True,
            'auto_clipboard': False,
            'between_dl_min': BETWEEN_DL_MIN,
            'between_dl_max': BETWEEN_DL_MAX,
            'subtitles': False,
            'subtitle_langs': 'tr,en',
            'subtitle_embed': True,
            'sponsorblock': False,
            'proxy': '',
            'output_template': '%(title)s.%(ext)s',
            'minimize_tray': False,
            'skip_duplicates': True,
        })
        self.history          = self._load(HISTORY_FILE, [])
        self.log              = self._load(LOG_FILE, [])
        self.downloaded_urls: set = set(self._load(DOWNLOADED_URLS_FILE, []))
        self.download_items: dict[str, DownloadItem] = {}
        self.failed_urls: list = []
        self.stop_event       = threading.Event()
        self.active           = False
        self._current_item    = None  # progress_hook için aktif öge

        # Durum çubuğu — 2 alan: genel mesaj | ilerleme metni
        self.CreateStatusBar(2)
        self.GetStatusBar().SetStatusWidths([-3, -2])
        self.speaker = StatusSpeaker(self.GetStatusBar())
        self.speaker.say("Uygulama hazır. F1 ile yardım alabilirsiniz.", 0)

        # Thread-safe UI güncelleme kuyruğu (NVDA uyumlu)
        self.ui_queue: queue.Queue = queue.Queue()
        self._ui_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._process_ui_queue, self._ui_timer)
        self._ui_timer.Start(50)  # Her 50ms'de kuyruk işlenir

        self.notebook = wx.Notebook(self)
        self._build_download_tab()
        self._build_settings_tab()
        self._build_history_tab()
        self._build_log_tab()
        self._setup_accelerators()

        self._clipboard_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._check_clipboard, self._clipboard_timer)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.tray_icon = None
        self.Bind(wx.EVT_ICONIZE, self._on_iconize)
        self._setup_tray_icon()
        self.Show()

        if self.settings.get('auto_clipboard'):
            self._clipboard_timer.Start(1500)

        # İlk çalıştırmada yt-dlp.exe yoksa GitHub'dan arka planda indir
        self._ytdlp_busy = False
        if not ytdlp_exists():
            self.speaker.say("yt-dlp.exe bulunamadı, GitHub'dan indiriliyor...", 0)
            self._ytdlp_busy = True
            threading.Thread(target=self._auto_download_ytdlp_on_start, daemon=True).start()

    # ── JSON yardımcıları ─────────────────────────────────────────────────────
    def _load(self, path, default):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def _save(self, path, data):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self._log_event(f"Kayıt hatası: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # SEKME 1: İNDİRME MERKEZİ
    # ─────────────────────────────────────────────────────────────────────────
    def _build_download_tab(self):
        panel = wx.Panel(self.notebook)
        panel.SetName("İndirme Merkezi paneli")
        sizer = wx.BoxSizer(wx.VERTICAL)

        # ── Arama kutusu ──────────────────────────────────────────────────────
        lbl_search = wx.StaticText(panel, label="Video Ara (isim veya anahtar kelime):")
        lbl_search.SetName("Arama etiketi")
        sizer.Add(lbl_search, 0, wx.LEFT | wx.TOP, 12)

        hbox_search = wx.BoxSizer(wx.HORIZONTAL)
        self.search_input = wx.TextCtrl(
            panel, size=(-1, -1),
            style=wx.TE_PROCESS_ENTER,
            name="Arama giriş alanı")
        self.search_input.SetHint("Örn: Bach Suite No 1, Python tutorial...")
        self.search_input.SetToolTip(
            "Aramak istediğiniz video adını veya anahtar kelimeleri yazın, "
            "ardından Ara butonuna basın.")
        self.search_input.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        hbox_search.Add(self.search_input, 1, wx.EXPAND | wx.RIGHT, 6)

        self.btn_search = wx.Button(panel, label="Ara", name="Arama butonu")
        self.btn_search.SetToolTip("YouTube'da arama yapar ve sonuçları listede gösterir.")
        self.btn_search.Bind(wx.EVT_BUTTON, self._on_search)
        hbox_search.Add(self.btn_search, 0)
        sizer.Add(hbox_search, 0, wx.ALL | wx.EXPAND, 10)

        lbl_results = wx.StaticText(panel, label="Arama Sonuçları (seçip İndir'e basın, çoklu seçim için Ctrl/Shift kullanın):")
        lbl_results.SetName("Sonuçlar etiketi")
        sizer.Add(lbl_results, 0, wx.LEFT, 10)

        self.search_results = wx.ListBox(
            panel, style=wx.LB_EXTENDED,
            name="Arama sonuçları listesi")
        self.search_results.SetToolTip(
            "Bulunan videolar listelenir. Bir veya birden fazla seçip "
            "İndirmeye Ekle butonuna basın. Çoklu seçim için Ctrl veya Shift kullanın.")
        self._search_result_data: list = []  # (title, url, channel) tuples
        sizer.Add(self.search_results, 1, wx.ALL | wx.EXPAND, 10)

        btn_add_from_search = wx.Button(
            panel, label="Seçilenleri İndirme Kuyruğuna Ekle",
            name="Seçilenleri ekle butonu")
        btn_add_from_search.SetToolTip(
            "Arama sonuçlarından seçtiğiniz videoları indirme kuyruğuna ekler.")
        btn_add_from_search.Bind(wx.EVT_BUTTON, self._on_add_from_search)
        sizer.Add(btn_add_from_search, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

        # ── Ayırıcı ──────────────────────────────────────────────────────────
        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(wx.StaticText(panel, label=""), 0, wx.BOTTOM, 4)

        # ── URL girişi (manuel) ───────────────────────────────────────────────
        lbl_url = wx.StaticText(
            panel,
            label="Veya Doğrudan URL Girin — Her satıra bir adres:")
        lbl_url.SetName("URL giriş etiketi")
        sizer.Add(lbl_url, 0, wx.LEFT, 12)

        self.url_input = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE, size=(-1, 70),
            name="URL giriş alanı")
        self.url_input.SetHint("https://www.youtube.com/watch?v=...")
        self.url_input.SetToolTip(
            "YouTube video veya oynatma listesi adreslerini buraya yapıştırın. "
            "Birden fazla adres girebilirsiniz; her satıra bir tane.")
        sizer.Add(self.url_input, 0, wx.ALL | wx.EXPAND, 10)

        # ── Biçim seçimi ─────────────────────────────────────────────────────
        hbox_fmt = wx.BoxSizer(wx.HORIZONTAL)
        lbl_fmt = wx.StaticText(panel, label="İndirme Biçimi:")
        hbox_fmt.Add(lbl_fmt, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.format_choice = wx.ComboBox(
            panel,
            choices=[
                "Ses — MP3 320kbps (En Yüksek Kalite)",
                "Ses — MP3 192kbps",
                "Ses — FLAC (Kayıpsız)",
                "Ses — Opus (En Yüksek Kalite)",
                "Ses — AAC/M4A 256kbps",
                "Video — MP4 4K (2160p)",
                "Video — MP4 1440p (2K)",
                "Video — MP4 1080p",
                "Video — MP4 720p",
                "Video — MP4 480p",
                "Video — MP4 En İyi Kalite",
                "Video — WebM En İyi Kalite",
                "Sadece Kapak Fotoğrafı (Thumbnail)",
            ],
            style=wx.CB_READONLY, name="Biçim seçim kutusu")
        self.format_choice.SetSelection(0)
        self.format_choice.SetToolTip(
            "Yön tuşlarıyla seçenekler arasında gezinin. "
            "Ses için MP3 veya FLAC, video için MP4 ya da WebM seçebilirsiniz.")
        hbox_fmt.Add(self.format_choice, 1, wx.EXPAND)
        sizer.Add(hbox_fmt, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

        # ── Butonlar ─────────────────────────────────────────────────────────
        hbox_btn = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_paste = wx.Button(panel, label="Panodan Yapıştır", name="Yapıştır butonu")
        self.btn_paste.SetToolTip("Panodaki YouTube adresini URL alanına ekler. (Ctrl+V da kullanılabilir)")
        self.btn_paste.Bind(wx.EVT_BUTTON, self._on_paste)
        hbox_btn.Add(self.btn_paste, 1, wx.EXPAND | wx.RIGHT, 4)

        self.btn_import = wx.Button(panel, label="Dosyadan İçe Aktar (.txt)  (Ctrl+O)",
                                    name="Dosyadan içe aktar butonu")
        self.btn_import.SetToolTip(
            "Her satırda bir adres bulunan bir .txt dosyasından toplu URL içe aktarır.")
        self.btn_import.Bind(wx.EVT_BUTTON, self._on_import_file)
        hbox_btn.Add(self.btn_import, 1, wx.EXPAND | wx.RIGHT, 4)

        self.btn_start = wx.Button(panel, label="İndirmeyi Başlat  (Ctrl+I)", name="Başlat butonu")
        self.btn_start.SetToolTip(
            "Girilen adresleri indirmeye başlar. "
            "İndirme sırasında uygulamayı kapatmayın.")
        self.btn_start.Bind(wx.EVT_BUTTON, self._on_start)
        hbox_btn.Add(self.btn_start, 1, wx.EXPAND | wx.RIGHT, 4)

        self.btn_cancel = wx.Button(panel, label="İptal Et  (Ctrl+Q)", name="İptal butonu")
        self.btn_cancel.SetToolTip("Devam eden tüm indirmeleri durdurur.")
        self.btn_cancel.Bind(wx.EVT_BUTTON, self._on_cancel)
        self.btn_cancel.Disable()
        hbox_btn.Add(self.btn_cancel, 1, wx.EXPAND | wx.RIGHT, 4)

        self.btn_retry = wx.Button(panel, label="Başarısızları Yeniden Dene  (Ctrl+R)",
                                   name="Yeniden dene butonu")
        self.btn_retry.SetToolTip(
            "Son indirme işleminde hata alan ögeleri tekrar kuyruğa alır ve indirmeye çalışır.")
        self.btn_retry.Bind(wx.EVT_BUTTON, self._on_retry)
        self.btn_retry.Disable()
        hbox_btn.Add(self.btn_retry, 1, wx.EXPAND)

        sizer.Add(hbox_btn, 0, wx.ALL | wx.EXPAND, 10)

        # ── İlerleme çubuğu ──────────────────────────────────────────────────
        self.progress_bar = wx.Gauge(
            panel, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH,
            name="Genel ilerleme göstergesi")
        self.progress_bar.SetToolTip(
            "Toplam indirme ilerlemesini gösterir. "
            "Ekran okuyucu kullanıcıları F5 tuşuyla sesli özet alabilir.")
        sizer.Add(self.progress_bar, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        sizer.Add(wx.StaticText(panel, label=""), 0, wx.BOTTOM, 4)

        # ── Kuyruk listesi ────────────────────────────────────────────────────
        lbl_queue = wx.StaticText(
            panel, label="İndirme Kuyruğu ve Durum  (F5 ile sesli özet, F6 ile başarısızları yeniden dene):")
        sizer.Add(lbl_queue, 0, wx.LEFT, 10)

        self.queue_list = wx.ListCtrl(
            panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
            name="İndirme kuyruğu listesi")
        self.queue_list.InsertColumn(0, "Başlık / Adres",  width=245)
        self.queue_list.InsertColumn(1, "Durum",           width=130)
        self.queue_list.InsertColumn(2, "İlerleme",        width=75)
        self.queue_list.InsertColumn(3, "Hız",             width=85)
        self.queue_list.InsertColumn(4, "Kalan Süre",      width=80)
        self.queue_list.InsertColumn(5, "Deneme",          width=65)
        self.queue_list.SetToolTip(
            "İndirme kuyruğu. Yön tuşlarıyla satırlar arasında gezin. "
            "F5 ile durum özetini sesli olarak dinleyin.")
        self.queue_list.Bind(wx.EVT_KEY_DOWN, self._on_queue_keydown)
        sizer.Add(self.queue_list, 1, wx.ALL | wx.EXPAND, 10)

        hbox_queue_mgmt = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_remove_item = wx.Button(
            panel, label="Seçileni Kuyruktan Sil  (Delete)", name="Seçileni sil butonu")
        self.btn_remove_item.SetToolTip(
            "Kuyrukta seçili olan ögeyi listeden kaldırır. Devam eden indirmeyi durdurmaz.")
        self.btn_remove_item.Bind(wx.EVT_BUTTON, self._on_remove_selected)
        hbox_queue_mgmt.Add(self.btn_remove_item, 1, wx.EXPAND | wx.RIGHT, 4)

        self.btn_clear_completed = wx.Button(
            panel, label="Tamamlananları Kuyruktan Temizle", name="Tamamlananları temizle butonu")
        self.btn_clear_completed.SetToolTip(
            "Durumu 'Tamamlandı' olan ögeleri kuyruk listesinden kaldırır.")
        self.btn_clear_completed.Bind(wx.EVT_BUTTON, self._on_clear_completed)
        hbox_queue_mgmt.Add(self.btn_clear_completed, 1, wx.EXPAND)
        sizer.Add(hbox_queue_mgmt, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "İndirme Merkezi  Alt+1")

    # ─────────────────────────────────────────────────────────────────────────
    # SEKME 2: AYARLAR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_settings_tab(self):
        panel = wx.Panel(self.notebook)
        panel.SetName("Ayarlar paneli")
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="Varsayılan İndirme Klasörü:"),
                  0, wx.ALL, 10)
        self.dir_picker = wx.DirPickerCtrl(
            panel,
            path=self.settings.get('dir', os.path.expanduser('~\\Downloads')),
            message="İndirme klasörünü seçin",
            name="Klasör seçici")
        self.dir_picker.SetToolTip(
            "İndirilen dosyaların kaydedileceği klasörü seçin. "
            "Enter veya Boşluk ile klasör seçme penceresini açabilirsiniz.")
        sizer.Add(self.dir_picker, 0, wx.ALL | wx.EXPAND, 10)

        # Hız sınırı
        hbox_speed = wx.BoxSizer(wx.HORIZONTAL)
        hbox_speed.Add(
            wx.StaticText(panel, label="İndirme Hız Sınırı (örn: 2M, 500K — boş bırakın = sınırsız):"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.txt_speed = wx.TextCtrl(
            panel, value=self.settings.get('speed_limit', ''),
            name="Hız sınırı giriş alanı")
        self.txt_speed.SetToolTip(
            "Örnek: 2M = saniyede 2 megabyte, 500K = saniyede 500 kilobyte. Boş bırakırsanız sınırsız.")
        hbox_speed.Add(self.txt_speed, 1)
        sizer.Add(hbox_speed, 0, wx.ALL | wx.EXPAND, 10)

        # İndirmeler arası bekleme
        sizer.Add(wx.StaticText(panel, label="İndirmeler Arası Bekleme Süresi (HTTP 429'a karşı koruma):"),
                  0, wx.LEFT | wx.TOP, 10)
        hbox_wait = wx.BoxSizer(wx.HORIZONTAL)
        hbox_wait.Add(wx.StaticText(panel, label="Min. saniye:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.spin_min = wx.SpinCtrl(panel, min=1, max=60,
                                    value=str(self.settings.get('between_dl_min', BETWEEN_DL_MIN)),
                                    name="Minimum bekleme süresi")
        self.spin_min.SetToolTip("Ardışık iki indirme arasındaki minimum bekleme süresi (saniye).")
        hbox_wait.Add(self.spin_min, 0, wx.RIGHT, 12)
        hbox_wait.Add(wx.StaticText(panel, label="Maks. saniye:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.spin_max = wx.SpinCtrl(panel, min=1, max=120,
                                    value=str(self.settings.get('between_dl_max', BETWEEN_DL_MAX)),
                                    name="Maksimum bekleme süresi")
        self.spin_max.SetToolTip("Ardışık iki indirme arasındaki maksimum bekleme süresi (saniye).")
        hbox_wait.Add(self.spin_max, 0)
        sizer.Add(hbox_wait, 0, wx.ALL, 10)

        # Onay kutuları
        checks = [
            ("chk_playlist",       "playlist",
             "Oynatma Listelerini (Playlist) Tümden İndir — Her video ayrı satırda izlenir",
             "Playlist URL'leri önce analiz edilir; her video ayrı kuyruk satırı olarak eklenir. "
             "Videolar PlaylistAdı/ alt klasörüne 001-başlık, 002-başlık şeklinde kaydedilir."),
            ("chk_meta",           "embed_meta",
             "Kapak Fotoğrafı ve Etiket Bilgilerini Göm",
             "MP3 dosyasına albüm kapağı ve sanatçı/başlık bilgisi eklenir."),
            ("chk_notifications",  "notifications",
             "İndirme Tamamlandığında Sesli Bildirim (Bip Sesi)",
             "Her indirme bittiğinde bir bip sesi çalar."),
            ("chk_auto_clipboard", "auto_clipboard",
             "Panodan Otomatik YouTube Adresi Algıla ve Yapıştır",
             "YouTube adresi kopyaladığınızda otomatik URL alanına eklenir."),
        ]
        for attr, key, label, tip in checks:
            chk = wx.CheckBox(panel, label=label, name=f"{attr} onay kutusu")
            chk.SetValue(self.settings.get(key, False))
            chk.SetToolTip(tip)
            setattr(self, attr, chk)
            sizer.Add(chk, 0, wx.LEFT | wx.BOTTOM, 10)

        # ── Gelişmiş Pro Seçenekleri ─────────────────────────────────────────
        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(wx.StaticText(panel, label="Gelişmiş Seçenekler:", name="Gelişmiş seçenekler başlığı"),
                  0, wx.LEFT | wx.TOP, 10)

        self.chk_subtitles = wx.CheckBox(
            panel, label="Altyazıları İndir",
            name="Altyazı onay kutusu")
        self.chk_subtitles.SetValue(self.settings.get('subtitles', False))
        self.chk_subtitles.SetToolTip(
            "Mevcutsa manuel, yoksa otomatik oluşturulan altyazıları indirir. "
            "YouTube'un altyazı sunucusu ayrıca hız sınırlı olduğu için "
            "istekler arasına otomatik olarak kısa bir bekleme eklenir.")
        sizer.Add(self.chk_subtitles, 0, wx.LEFT | wx.TOP, 10)

        hbox_sub = wx.BoxSizer(wx.HORIZONTAL)
        hbox_sub.Add(wx.StaticText(panel, label="Altyazı Dilleri (virgülle ayırın):"),
                     0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.txt_sub_langs = wx.TextCtrl(
            panel, value=self.settings.get('subtitle_langs', 'tr,en'),
            name="Altyazı dilleri giriş alanı")
        self.txt_sub_langs.SetToolTip("Örnek: tr,en — Türkçe ve İngilizce altyazı indirir.")
        hbox_sub.Add(self.txt_sub_langs, 1)
        sizer.Add(hbox_sub, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 10)

        self.chk_sub_embed = wx.CheckBox(
            panel, label="Altyazıyı Videonun İçine Göm (kapalıysa ayrı .srt dosyası olur)",
            name="Altyazı gömme onay kutusu")
        self.chk_sub_embed.SetValue(self.settings.get('subtitle_embed', True))
        sizer.Add(self.chk_sub_embed, 0, wx.LEFT | wx.TOP | wx.BOTTOM, 10)

        self.chk_sponsorblock = wx.CheckBox(
            panel, label="SponsorBlock — Sponsor/Tanıtım Bölümlerini Otomatik Atla (video)",
            name="SponsorBlock onay kutusu")
        self.chk_sponsorblock.SetValue(self.settings.get('sponsorblock', False))
        self.chk_sponsorblock.SetToolTip(
            "Topluluk tarafından işaretlenmiş sponsor/tanıtım bölümlerini videodan otomatik keser.")
        sizer.Add(self.chk_sponsorblock, 0, wx.LEFT | wx.BOTTOM, 10)

        hbox_proxy = wx.BoxSizer(wx.HORIZONTAL)
        hbox_proxy.Add(wx.StaticText(panel, label="Proxy Adresi (isteğe bağlı, örn: http://127.0.0.1:8080):"),
                       0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.txt_proxy = wx.TextCtrl(
            panel, value=self.settings.get('proxy', ''), name="Proxy giriş alanı")
        self.txt_proxy.SetToolTip("Boş bırakırsanız doğrudan bağlantı kullanılır.")
        hbox_proxy.Add(self.txt_proxy, 1)
        sizer.Add(hbox_proxy, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 10)

        hbox_tmpl = wx.BoxSizer(wx.HORIZONTAL)
        hbox_tmpl.Add(wx.StaticText(panel, label="Dosya Adı Şablonu:"),
                     0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.txt_template = wx.TextCtrl(
            panel, value=self.settings.get('output_template', '%(title)s.%(ext)s'),
            name="Dosya adı şablonu giriş alanı")
        self.txt_template.SetToolTip(
            "yt-dlp çıktı şablonu. Örnekler: %(title)s.%(ext)s | "
            "%(uploader)s - %(title)s.%(ext)s | %(upload_date)s - %(title)s.%(ext)s")
        hbox_tmpl.Add(self.txt_template, 1)
        sizer.Add(hbox_tmpl, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 10)

        self.chk_skip_dup = wx.CheckBox(
            panel, label="Geçmişte Zaten İndirilmiş Adresleri Kuyruğa Eklerken Uyar",
            name="Yinelenenleri atla onay kutusu")
        self.chk_skip_dup.SetValue(self.settings.get('skip_duplicates', True))
        sizer.Add(self.chk_skip_dup, 0, wx.LEFT | wx.TOP | wx.BOTTOM, 10)

        if HAS_WX_ADV:
            self.chk_minimize_tray = wx.CheckBox(
                panel, label="Pencere Küçültüldüğünde Sistem Tepsisine Gizle",
                name="Tepsiye küçültme onay kutusu")
            self.chk_minimize_tray.SetValue(self.settings.get('minimize_tray', False))
            self.chk_minimize_tray.SetToolTip(
                "Etkinleştirilirse, pencereyi küçülttüğünüzde uygulama görev çubuğu yerine "
                "sistem tepsisinde simge olarak çalışmaya devam eder.")
            sizer.Add(self.chk_minimize_tray, 0, wx.LEFT | wx.BOTTOM, 10)
        else:
            self.chk_minimize_tray = None

        btn_save = wx.Button(panel, label="Ayarları Kaydet  (Ctrl+S)", name="Ayarları kaydet butonu")
        btn_save.SetToolTip("Tüm ayarları diske kaydeder.")
        btn_save.Bind(wx.EVT_BUTTON, self._on_save_settings)
        sizer.Add(btn_save, 0, wx.ALL | wx.EXPAND, 10)

        ffmpeg = get_ffmpeg_path()
        ff_msg = ("FFmpeg BULUNDU — Ses/video dönüştürme ve etiket özellikleri aktif."
                  if ffmpeg else
                  "UYARI: FFmpeg bulunamadı — ffmpeg.exe ve ffprobe.exe dosyalarını program klasörüne kopyalayın.")
        lbl_ff = wx.StaticText(panel, label=ff_msg, name="FFmpeg durum etiketi")
        sizer.Add(lbl_ff, 0, wx.ALL, 10)

        # ── yt-dlp motoru: durum ve güncelleme ──────────────────────────────
        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(wx.StaticText(panel, label=""), 0, wx.BOTTOM, 2)

        self.lbl_ytdlp = wx.StaticText(
            panel, label=self._ytdlp_status_text(), name="yt-dlp durum etiketi")
        self.lbl_ytdlp.SetToolTip(
            "İndirme motoru olarak kullanılan yt-dlp.exe dosyasının durumu ve sürümü.")
        sizer.Add(self.lbl_ytdlp, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        self.btn_update_ytdlp = wx.Button(
            panel, label="yt-dlp'yi Güncelle / İndir  (GitHub)",
            name="yt-dlp güncelle butonu")
        self.btn_update_ytdlp.SetToolTip(
            "yt-dlp.exe programını GitHub'daki resmi yt-dlp/yt-dlp deposundan "
            "en güncel sürüme günceller. Dosya hiç indirilmemişse önce indirir.")
        self.btn_update_ytdlp.Bind(wx.EVT_BUTTON, self._on_update_ytdlp)
        sizer.Add(self.btn_update_ytdlp, 0, wx.ALL | wx.EXPAND, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Ayarlar  Alt+2")

    def _ytdlp_status_text(self) -> str:
        if not ytdlp_exists():
            return ("yt-dlp motoru: BULUNAMADI — aşağıdaki butonla GitHub'dan indirin.")
        ver = get_ytdlp_version()
        return f"yt-dlp motoru sürümü: {ver or 'bilinmiyor'}   ({YTDLP_EXE})"

    # ─────────────────────────────────────────────────────────────────────────
    # SEKME 3: GEÇMİŞ
    # ─────────────────────────────────────────────────────────────────────────
    def _build_history_tab(self):
        panel = wx.Panel(self.notebook)
        panel.SetName("Geçmiş paneli")
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="İndirilen Dosyalar Geçmişi (en yeni üstte):"),
                  0, wx.ALL, 10)
        self.history_list = wx.ListBox(panel, name="Geçmiş listesi")
        self.history_list.SetToolTip(
            "Daha önce indirilen dosyaların listesi. Yön tuşlarıyla gezin.")
        self._refresh_history()
        sizer.Add(self.history_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        btn_open = wx.Button(panel, label="İndirme Klasörünü Aç", name="Klasörü aç butonu")
        btn_open.SetToolTip("İndirme klasörünü Dosya Gezgini ile açar.")
        btn_open.Bind(wx.EVT_BUTTON, self._on_open_folder)
        hbox.Add(btn_open, 1, wx.EXPAND | wx.RIGHT, 5)

        btn_clear = wx.Button(panel, label="Geçmişi Temizle", name="Geçmişi temizle butonu")
        btn_clear.SetToolTip("Geçmiş listesini siler. Disk üzerindeki dosyalar etkilenmez.")
        btn_clear.Bind(wx.EVT_BUTTON, self._on_clear_history)
        hbox.Add(btn_clear, 1, wx.EXPAND)
        sizer.Add(hbox, 0, wx.ALL | wx.EXPAND, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Geçmiş  Alt+3")

    # ─────────────────────────────────────────────────────────────────────────
    # SEKME 4: İŞLEM GÜNLÜĞÜ
    # ─────────────────────────────────────────────────────────────────────────
    def _build_log_tab(self):
        panel = wx.Panel(self.notebook)
        panel.SetName("Günlük paneli")
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="İşlem Günlüğü — Zaman Damgalı Tüm Olaylar:"),
                  0, wx.ALL, 10)
        self.log_text = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
            name="İşlem günlüğü metin alanı")
        self.log_text.SetToolTip(
            "Tüm indirme olaylarının zaman damgalı kaydı. Yön tuşları ve Page Up/Down ile gezin.")
        self._refresh_log()
        sizer.Add(self.log_text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        hbox_log = wx.BoxSizer(wx.HORIZONTAL)
        btn_export = wx.Button(panel, label="Günlüğü Dışa Aktar (.txt)  (Ctrl+E)",
                               name="Günlüğü dışa aktar butonu")
        btn_export.SetToolTip(
            "İşlem günlüğünü masaüstüne metin dosyası olarak kaydeder.")
        btn_export.Bind(wx.EVT_BUTTON, self._on_export_log)
        hbox_log.Add(btn_export, 1, wx.EXPAND | wx.RIGHT, 5)

        btn_clear_log = wx.Button(panel, label="Günlüğü Temizle",
                                  name="Günlük temizle butonu")
        btn_clear_log.SetToolTip("İşlem günlüğünü temizler. Geçmiş listesi etkilenmez.")
        btn_clear_log.Bind(wx.EVT_BUTTON, self._on_clear_log)
        hbox_log.Add(btn_clear_log, 1, wx.EXPAND)
        sizer.Add(hbox_log, 0, wx.ALL | wx.EXPAND, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Günlük  Alt+4")

    # ─────────────────────────────────────────────────────────────────────────
    # KLAVYE KISAYOLLARI
    # ─────────────────────────────────────────────────────────────────────────
    def _setup_accelerators(self):
        id_save   = wx.NewIdRef()
        id_help   = wx.NewIdRef()
        id_f5     = wx.NewIdRef()
        id_f6     = wx.NewIdRef()
        id_export = wx.NewIdRef()
        id_import = wx.NewIdRef()
        id_tab1   = wx.NewIdRef()
        id_tab2   = wx.NewIdRef()
        id_tab3   = wx.NewIdRef()
        id_tab4   = wx.NewIdRef()

        entries = [
            (wx.ACCEL_CTRL,   ord('I'), self.btn_start.GetId()),
            (wx.ACCEL_CTRL,   ord('Q'), self.btn_cancel.GetId()),
            (wx.ACCEL_CTRL,   ord('R'), self.btn_retry.GetId()),
            (wx.ACCEL_CTRL,   ord('S'), id_save),
            (wx.ACCEL_CTRL,   ord('E'), id_export),
            (wx.ACCEL_CTRL,   ord('O'), id_import),
            (wx.ACCEL_NORMAL, wx.WXK_F1, id_help),
            (wx.ACCEL_NORMAL, wx.WXK_F5, id_f5),
            (wx.ACCEL_NORMAL, wx.WXK_F6, id_f6),
            (wx.ACCEL_ALT,    ord('1'), id_tab1),
            (wx.ACCEL_ALT,    ord('2'), id_tab2),
            (wx.ACCEL_ALT,    ord('3'), id_tab3),
            (wx.ACCEL_ALT,    ord('4'), id_tab4),
        ]
        self.SetAcceleratorTable(wx.AcceleratorTable(entries))
        self.Bind(wx.EVT_MENU, self._on_save_settings,                    id=id_save)
        self.Bind(wx.EVT_MENU, self._on_help,                             id=id_help)
        self.Bind(wx.EVT_MENU, self._on_read_status,                      id=id_f5)
        self.Bind(wx.EVT_MENU, self._on_retry,                            id=id_f6)
        self.Bind(wx.EVT_MENU, self._on_export_log,                       id=id_export)
        self.Bind(wx.EVT_MENU, self._on_import_file,                      id=id_import)
        self.Bind(wx.EVT_MENU, lambda e: self.notebook.SetSelection(0),   id=id_tab1)
        self.Bind(wx.EVT_MENU, lambda e: self.notebook.SetSelection(1),   id=id_tab2)
        self.Bind(wx.EVT_MENU, lambda e: self.notebook.SetSelection(2),   id=id_tab3)
        self.Bind(wx.EVT_MENU, lambda e: self.notebook.SetSelection(3),   id=id_tab4)

    # ─────────────────────────────────────────────────────────────────────────
    # OLAY İŞLEYİCİLERİ
    # ─────────────────────────────────────────────────────────────────────────
    def _on_paste(self, event):
        if wx.TheClipboard.Open():
            data = wx.TextDataObject()
            if wx.TheClipboard.GetData(data):
                text = data.GetText().strip()
                current = self.url_input.GetValue().strip()
                sep = "\n" if current else ""
                self.url_input.SetValue(current + sep + text)
                self.url_input.SetInsertionPointEnd()
                self.speaker.say("Pano içeriği URL alanına yapıştırıldı.")
            wx.TheClipboard.Close()

    def _on_import_file(self, event):
        with wx.FileDialog(
            self, "URL listesi içeren .txt dosyasını seçin",
            wildcard="Metin dosyaları (*.txt)|*.txt|Tüm dosyalar (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as exc:
            bildir(self, f"Dosya okunamadı:\n{exc}", "Hata", wx.ICON_ERROR)
            return
        urls = normalize_urls(content)
        if not urls:
            bildir(self, "Dosyada geçerli bir adres bulunamadı.", "Uyarı", wx.ICON_WARNING)
            return
        current = self.url_input.GetValue().strip()
        sep = "\n" if current else ""
        self.url_input.SetValue(current + sep + "\n".join(urls))
        self.url_input.SetInsertionPointEnd()
        self.speaker.say(f"{len(urls)} adres dosyadan içe aktarıldı.", 0)
        self._log_event(f"Dosyadan {len(urls)} adres içe aktarıldı: {os.path.basename(path)}")

    def _on_remove_selected(self, event):
        idx = self.queue_list.GetFirstSelected()
        if idx == wx.NOT_FOUND:
            bildir(self, "Silmek için önce kuyruktan bir öge seçin.", "Bilgi")
            return
        if self.active:
            title = self.queue_list.GetItemText(idx, 0)
            status = self.queue_list.GetItemText(idx, 1)
            if status in ("İndiriliyor", "Bağlanıyor...", "Dönüştürülüyor"):
                bildir(self, "Şu anda indirilmekte olan öge kuyruktan silinemez.",
                       "Uyarı", wx.ICON_WARNING)
                return
        removed_title = self.queue_list.GetItemText(idx, 0)
        self.queue_list.DeleteItem(idx)
        self.speaker.say(f"Kuyruktan silindi: {removed_title[:40]}", 0)

    def _on_clear_completed(self, event):
        removed = 0
        i = 0
        while i < self.queue_list.GetItemCount():
            status = self.queue_list.GetItemText(i, 1)
            if "Tamamlandı" in status:
                self.queue_list.DeleteItem(i)
                removed += 1
            else:
                i += 1
        self.speaker.say(f"{removed} tamamlanmış öge kuyruktan temizlendi.", 0)

    def _on_start(self, event):
        if not ytdlp_exists():
            bildir(self,
                   "yt-dlp motoru (yt-dlp.exe) henüz indirilmedi.\n"
                   "Ayarlar sekmesinden 'yt-dlp'yi Güncelle / İndir' butonuna basın.",
                   "Uyarı", wx.ICON_WARNING)
            self.notebook.SetSelection(1)
            return

        raw = self.url_input.GetValue()
        urls = normalize_urls(raw)
        if not urls:
            bildir(self, "Lütfen en az bir YouTube adresi girin.", "Uyarı", wx.ICON_WARNING)
            self.url_input.SetFocus()
            return

        invalid = [u for u in urls if not is_valid_url(u)]
        if invalid:
            msg = ("Aşağıdaki adresler geçerli YouTube adresi gibi görünmüyor:\n\n"
                   + "\n".join(invalid[:5])
                   + ("\n..." if len(invalid) > 5 else "")
                   + "\n\nYine de devam edilsin mi?")
            dlg = wx.MessageDialog(self, msg, "Geçersiz Adres", wx.YES_NO | wx.ICON_WARNING)
            result = dlg.ShowModal()
            dlg.Destroy()
            if result != wx.ID_YES:
                return

        if self.settings.get('skip_duplicates', True):
            already = [u for u in urls if u in self.downloaded_urls]
            if already:
                msg = (f"{len(already)} adres daha önce bu programla indirilmiş görünüyor:\n\n"
                       + "\n".join(already[:5])
                       + ("\n..." if len(already) > 5 else "")
                       + "\n\nBunları yeniden indirmek ister misiniz?")
                dlg = wx.MessageDialog(self, msg, "Yinelenen Adres", wx.YES_NO | wx.ICON_QUESTION)
                result = dlg.ShowModal()
                dlg.Destroy()
                if result != wx.ID_YES:
                    urls = [u for u in urls if u not in already]
                    if not urls:
                        self.speaker.say("Tüm adresler daha önce indirilmiş; kuyruk boş.", 0)
                        return

        self._start_downloads(urls)

    def _on_retry(self, event):
        if self.active:
            bildir(self, "Şu an bir indirme devam ediyor. Bitmesini bekleyin.", "Bilgi")
            return
        if not self.failed_urls:
            bildir(self, "Yeniden denenecek başarısız öge bulunamadı.", "Bilgi")
            return
        urls = list(self.failed_urls)
        self.failed_urls.clear()
        self._start_downloads(urls)

    def _start_downloads(self, urls: list):
        self.queue_list.DeleteAllItems()
        self.download_items.clear()
        self.failed_urls.clear()
        self.stop_event.clear()
        self.active = True
        self.progress_bar.SetValue(0)
        self.btn_start.Disable()
        self.btn_cancel.Enable()
        self.btn_retry.Disable()
        self.url_input.Clear()
        self.speaker.say(f"{len(urls)} adres kuyruğa eklendi. İndirme başlıyor.", 0)
        threading.Thread(
            target=self._process_downloads,
            args=(urls,),
            daemon=True
        ).start()

    def _on_cancel(self, event):
        self.stop_event.set()
        self.btn_cancel.Disable()
        self.speaker.say("İptal sinyali gönderildi. Mevcut indirme tamamlandıktan sonra durulacak.", 0)

    def _on_save_settings(self, event):
        mn = self.spin_min.GetValue()
        mx = self.spin_max.GetValue()
        if mn > mx:
            bildir(self, "Minimum bekleme süresi, maksimum değerden büyük olamaz.", "Hata", wx.ICON_ERROR)
            return
        template = self.txt_template.GetValue().strip() or '%(title)s.%(ext)s'
        self.settings.update({
            'dir':             self.dir_picker.GetPath(),
            'playlist':        self.chk_playlist.GetValue(),
            'embed_meta':      self.chk_meta.GetValue(),
            'speed_limit':     self.txt_speed.GetValue().strip(),
            'notifications':   self.chk_notifications.GetValue(),
            'auto_clipboard':  self.chk_auto_clipboard.GetValue(),
            'between_dl_min':  mn,
            'between_dl_max':  mx,
            'subtitles':       self.chk_subtitles.GetValue(),
            'subtitle_langs':  self.txt_sub_langs.GetValue().strip() or 'tr,en',
            'subtitle_embed':  self.chk_sub_embed.GetValue(),
            'sponsorblock':    self.chk_sponsorblock.GetValue(),
            'proxy':           self.txt_proxy.GetValue().strip(),
            'output_template': template,
            'skip_duplicates': self.chk_skip_dup.GetValue(),
            'minimize_tray':   self.chk_minimize_tray.GetValue() if self.chk_minimize_tray else False,
        })
        self._save(SETTINGS_FILE, self.settings)
        if self.settings['auto_clipboard']:
            self._clipboard_timer.Start(1500)
        else:
            self._clipboard_timer.Stop()
        self._setup_tray_icon()
        bildir(self, "Ayarlar başarıyla kaydedildi.")
        self.speaker.say("Ayarlar kaydedildi.", 0)

    # ─────────────────────────────────────────────────────────────────────────
    # YT-DLP MOTORU: GITHUB'DAN İNDİRME / GÜNCELLEME
    # ─────────────────────────────────────────────────────────────────────────
    def _open_ytdlp_progress_dialog(self, title: str) -> "YtdlpProgressDialog":
        dlg = YtdlpProgressDialog(self, title=title)
        dlg.Show()
        dlg.Raise()
        return dlg

    def _ytdlp_progress_cb(self, pct, downloaded=0, total=0):
        """download_ytdlp_exe içinden (arka plan thread'inden) çağrılır."""
        if pct is None:
            mb = downloaded / (1024 * 1024)
            msg = f"İndiriliyor... {mb:.1f} MB"
        else:
            mb = downloaded / (1024 * 1024)
            total_mb = (total or 0) / (1024 * 1024)
            msg = f"İndiriliyor... {mb:.1f} / {total_mb:.1f} MB"
        self._ui_update(self._apply_ytdlp_progress, pct, msg)

    def _apply_ytdlp_progress(self, pct, msg):
        if getattr(self, '_ytdlp_progress_dlg', None):
            self._ytdlp_progress_dlg.set_progress(pct, msg)
        self.speaker.say(msg, 0)

    def _close_ytdlp_progress_dialog(self):
        dlg = getattr(self, '_ytdlp_progress_dlg', None)
        if dlg:
            dlg.close_dialog()
            self._ytdlp_progress_dlg = None

    def _auto_download_ytdlp_on_start(self):
        """Program ilk açıldığında yt-dlp.exe yoksa klasik indirme penceresiyle indirir."""
        self._ui_update(self._ui_open_ytdlp_dialog, "yt-dlp Motoru İndiriliyor")
        try:
            download_ytdlp_exe(progress_cb=self._ytdlp_progress_cb)
            self._log_event("yt-dlp.exe GitHub'dan otomatik olarak indirildi (ilk çalıştırma).")
            self._ui_update(self.speaker.say, "yt-dlp.exe hazır. İndirmeye başlayabilirsiniz.", 0)
        except Exception as exc:
            self._log_event(f"yt-dlp.exe otomatik indirme hatası: {exc}")
            self._ui_update(
                self.speaker.say,
                "yt-dlp.exe otomatik indirilemedi. Ayarlar sekmesinden manuel deneyin.", 0)
        finally:
            self._ytdlp_busy = False
            self._ui_update(self._close_ytdlp_progress_dialog)
            self._ui_update(self._refresh_ytdlp_label)

    def _ui_open_ytdlp_dialog(self, title):
        """Ana thread'de çağrılır: wx pencereleri sadece UI thread'inde oluşturulabilir."""
        self._ytdlp_progress_dlg = self._open_ytdlp_progress_dialog(title)

    def _refresh_ytdlp_label(self):
        if hasattr(self, 'lbl_ytdlp'):
            self.lbl_ytdlp.SetLabel(self._ytdlp_status_text())

    def _on_update_ytdlp(self, event):
        if self._ytdlp_busy:
            bildir(self, "yt-dlp motoru şu anda indiriliyor/güncelleniyor, lütfen bekleyin.", "Bilgi")
            return
        self._ytdlp_busy = True
        self.btn_update_ytdlp.Disable()

        # Klasik indirme penceresini aç; indirme bitene kadar açık kalır
        self._ytdlp_progress_dlg = self._open_ytdlp_progress_dialog("yt-dlp Güncelleniyor")
        self._ytdlp_progress_dlg.set_progress(None, "GitHub'a bağlanılıyor...")
        self.speaker.say("yt-dlp motoru güncelleniyor / indiriliyor...", 0)
        threading.Thread(target=self._update_ytdlp_worker, daemon=True).start()

    def _update_ytdlp_worker(self):
        try:
            if not ytdlp_exists():
                self._ui_update(self._apply_ytdlp_progress, None, "yt-dlp.exe GitHub'dan indiriliyor...")
                download_ytdlp_exe(progress_cb=self._ytdlp_progress_cb)
                self._log_event("yt-dlp.exe GitHub'dan ilk kez indirildi.")
                result_msg = "yt-dlp.exe başarıyla indirildi."
            else:
                # Önce yt-dlp'nin kendi resmi güncelleme mekanizmasını dene (-U)
                self._ui_update(self._apply_ytdlp_progress, None, "Güncelleme kontrol ediliyor...")
                proc = subprocess.run(
                    [YTDLP_EXE, '-U'], capture_output=True, text=True,
                    timeout=90, creationflags=CREATE_NO_WINDOW)
                out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
                self._log_event(f"yt-dlp güncelleme çıktısı: {out[:200]}")
                if proc.returncode == 0:
                    result_msg = out or "yt-dlp zaten güncel."
                    self._ui_update(self._apply_ytdlp_progress, 100, "Güncel.")
                else:
                    # -U bazı durumlarda çalışmayabilir; GitHub'dan yeniden indir
                    self._ui_update(
                        self._apply_ytdlp_progress, None,
                        "Dahili güncelleme başarısız, GitHub'dan yeniden indiriliyor...")
                    download_ytdlp_exe(progress_cb=self._ytdlp_progress_cb)
                    self._log_event("yt-dlp.exe GitHub'dan yeniden indirilerek güncellendi.")
                    result_msg = "yt-dlp.exe GitHub'dan yeniden indirilerek güncellendi."

            ver = get_ytdlp_version()
            self._ui_update(
                self._on_ytdlp_update_done, True,
                f"{result_msg}\n\nGüncel sürüm: {ver or 'bilinmiyor'}")
        except Exception as exc:
            self._log_event(f"yt-dlp güncelleme hatası: {exc}")
            self._ui_update(self._on_ytdlp_update_done, False, str(exc))

    def _on_ytdlp_update_done(self, success: bool, msg: str):
        self._ytdlp_busy = False
        self.btn_update_ytdlp.Enable()
        self._refresh_ytdlp_label()
        self._close_ytdlp_progress_dialog()
        if success:
            self.speaker.say("yt-dlp güncelleme işlemi tamamlandı.", 0)
            bildir(self, msg, "yt-dlp Güncelleme")
        else:
            self.speaker.say("yt-dlp güncelleme işlemi başarısız oldu.", 0)
            bildir(self, f"Güncelleme sırasında hata oluştu:\n{msg[:300]}", "Hata", wx.ICON_ERROR)

    def _on_open_folder(self, event):
        folder = self.settings.get('dir', os.getcwd())
        if os.path.isdir(folder):
            os.startfile(folder)
        else:
            bildir(self, f"Klasör bulunamadı:\n{folder}", "Hata", wx.ICON_ERROR)

    def _on_clear_history(self, event):
        dlg = wx.MessageDialog(self, "Tüm geçmiş silinecek. Onaylıyor musunuz?",
                               "Onay", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
        if dlg.ShowModal() == wx.ID_YES:
            self.history = []
            self._save(HISTORY_FILE, self.history)
            self._refresh_history()
            self.speaker.say("Geçmiş temizlendi.", 0)
        dlg.Destroy()

    def _on_clear_log(self, event):
        self.log = []
        self._save(LOG_FILE, self.log)
        self._refresh_log()
        self.speaker.say("İşlem günlüğü temizlendi.", 0)

    def _on_export_log(self, event):
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        fname = f"youtube_indirici_gunluk_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        fpath = os.path.join(desktop, fname)
        try:
            lines = [f"[{e['zaman']}] {e['mesaj']}" for e in self.log]
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines))
            bildir(self, f"Günlük masaüstüne kaydedildi:\n{fpath}", "Dışa Aktar")
            self.speaker.say("Günlük dosyası masaüstüne kaydedildi.", 0)
        except Exception as e:
            bildir(self, f"Kayıt sırasında hata oluştu:\n{e}", "Hata", wx.ICON_ERROR)

    def _on_help(self, event):
        lines = ["KLAVYE KISAYOLLARI", "=" * 44]
        for k, v in KISAYOLLAR.items():
            lines.append(f"  {k:26s}  {v}")
        lines += [
            "",
            "EKRAN OKUYUCU İPUÇLARI",
            "=" * 44,
            "• Her kontrol için araç ipucu (tooltip) tanımlanmıştır.",
            "  NVDA: Insert+Numpad5  |  JAWS: Insert+F1",
            "• F5: İndirme kuyruğunu sesli özet olarak dinle.",
            "• F6 veya Ctrl+R: Başarısız ögeleri yeniden dene.",
            "• Alt+1..4: Sekmeler arası hızlı geçiş.",
            "• Pano izleme aktifse YouTube adresi kopyaladığınızda",
            "  URL alanına otomatik eklenir.",
            "• İndirme bitince bip sesi çalar (Ayarlar'dan açılabilir).",
            "",
            "OYNATMA LİSTESİ (PLAYLIST)",
            "=" * 44,
            "• Ayarlar'dan 'Oynatma Listelerini İndir' seçeneğini açın.",
            "• Playlist URL'si girildiğinde videolar önce analiz edilir:",
            "  her video ayrı bir kuyruk satırı olarak eklenir.",
            "• Videolar  IndirmeKlasörü/PlaylistAdı/  alt klasörüne",
            "  001-VideoAdı, 002-VideoAdı ... şeklinde kaydedilir.",
            "• Karışık listede (bazı video, bazı playlist) her öge",
            "  kendi türüne göre işlenir.",
            "",
            "=" * 44,
            "• İndirmeler arası otomatik rastgele bekleme uygulanır.",
            "• 429 hatası alındığında üstel geri çekilme (backoff) ile",
            f"  en fazla {RETRY_MAX} kez yeniden denenir.",
            "• 'Format bulunamadı' / bot-koruması gibi geçici hatalarda da",
            "  farklı bir tarayıcı kimliğiyle (User-Agent) otomatik",
            "  olarak yeniden denenir.",
            "• Her denemede farklı bir tarayıcı kimliği (User-Agent) kullanılır.",
            "• Bekleme sürelerini Ayarlar sekmesinden özelleştirebilirsiniz.",
            "",
            "PRO ÖZELLİKLER (v3.0)",
            "=" * 44,
            "• Altyazı indirme: Ayarlar sekmesinden dil ve gömme seçilebilir.",
            "• SponsorBlock: Sponsor/tanıtım bölümlerini otomatik keser.",
            "• Proxy: Ayarlar sekmesinden HTTP/SOCKS proxy adresi girilebilir.",
            "• Dosya adı şablonu: %(title)s, %(uploader)s, %(upload_date)s vb.",
            "• Ctrl+O: Bir .txt dosyasından toplu adres içe aktarır.",
            "• Delete: Kuyrukta seçili ögeyi listeden siler (indirmeyi durdurmaz).",
            "• Yinelenen adres uyarısı: Daha önce indirilen adresler için sorar.",
            "• Sistem tepsisi: Ayarlar'dan açılırsa pencere küçültüldüğünde",
            "  görev çubuğu yerine tepsiye gizlenir.",
        ]
        bildir(self, "\n".join(lines), "Yardım — F1")

    def _on_read_status(self, event):
        total = self.queue_list.GetItemCount()
        if total == 0:
            bildir(self, "Kuyrukta henüz öge yok.", "Durum — F5")
            return
        completed = sum(
            1 for i in range(total)
            if "Tamamlandı" in self.queue_list.GetItemText(i, 1)
        )
        failed = sum(
            1 for i in range(total)
            if "Hata" in self.queue_list.GetItemText(i, 1)
        )
        lines = [f"Toplam {total} öge — Tamamlanan: {completed}, Hatalı: {failed}\n"]
        for i in range(total):
            t = self.queue_list.GetItemText(i, 0)
            s = self.queue_list.GetItemText(i, 1)
            p = self.queue_list.GetItemText(i, 2)
            d = self.queue_list.GetItemText(i, 5)
            lines.append(f"{i+1}. {t[:55]}  —  {s}  {p}  {d}")
        bildir(self, "\n".join(lines), "İndirme Durumu — F5")

    def _on_queue_keydown(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_F5:
            self._on_read_status(event)
        elif key == wx.WXK_F6:
            self._on_retry(event)
        elif key == wx.WXK_DELETE:
            self._on_remove_selected(event)
        event.Skip()

    def _setup_tray_icon(self):
        if not HAS_WX_ADV:
            return
        want_tray = self.settings.get('minimize_tray', False)
        if want_tray and not self.tray_icon:
            self.tray_icon = AppTrayIcon(self)
        elif not want_tray and self.tray_icon:
            self.tray_icon.RemoveIcon()
            self.tray_icon.Destroy()
            self.tray_icon = None

    def _on_iconize(self, event):
        if event.IsIconized() and self.settings.get('minimize_tray', False) and self.tray_icon:
            self.Hide()
        event.Skip()

    def _on_close(self, event):
        if self.active:
            dlg = wx.MessageDialog(
                self,
                "İndirme devam ediyor. Şimdi çıkmak istiyor musunuz?\n"
                "(Evet seçilirse indirme durdurulur)",
                "Çıkış",
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            if dlg.ShowModal() != wx.ID_YES:
                dlg.Destroy()
                return
            self.stop_event.set()
            dlg.Destroy()
        self._clipboard_timer.Stop()
        self._ui_timer.Stop()
        if self.tray_icon:
            self.tray_icon.RemoveIcon()
            self.tray_icon.Destroy()
        self.Destroy()

    def _check_clipboard(self, event):
        if self.active:
            return
        if wx.TheClipboard.Open():
            data = wx.TextDataObject()
            if wx.TheClipboard.GetData(data):
                text = data.GetText().strip()
                if is_valid_url(text) and text not in self.url_input.GetValue():
                    current = self.url_input.GetValue().strip()
                    sep = "\n" if current else ""
                    self.url_input.SetValue(current + sep + text)
                    self.url_input.SetInsertionPointEnd()
                    self.speaker.say("YouTube adresi otomatik yapıştırıldı.", 0)
            wx.TheClipboard.Close()

    # ─────────────────────────────────────────────────────────────────────────
    # THREAD-SAFE UI GÜNCELLEME (Queue tabanlı — NVDA uyumlu)
    # ─────────────────────────────────────────────────────────────────────────
    def _ui_update(self, fn, *args):
        """Thread'den güvenli UI güncellemesi için kuyruğa koy."""
        self.ui_queue.put((fn, args))

    def _process_ui_queue(self, event):
        """50ms'de bir wx.Timer tarafından tetiklenir; kuyruktaki tüm UI işlerini uygular."""
        try:
            while True:
                fn, args = self.ui_queue.get_nowait()
                fn(*args)
        except queue.Empty:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # ARAMA ÖZELLİĞİ
    # ─────────────────────────────────────────────────────────────────────────
    def _on_search(self, event):
        query = self.search_input.GetValue().strip()
        if not query:
            bildir(self, "Lütfen bir arama terimi girin.", "Uyarı", wx.ICON_WARNING)
            self.search_input.SetFocus()
            return
        self.btn_search.Disable()
        self.search_results.Clear()
        self._search_result_data = []
        self.speaker.say(f"Aranıyor: {query}", 0)
        threading.Thread(
            target=self._do_search,
            args=(query,),
            daemon=True
        ).start()

    def _ytdlp_dump_json(self, target: str, ua: str) -> dict:
        """
        yt-dlp.exe'yi --dump-single-json ile çalıştırıp tek bir JSON nesnesi döndürür.
        Arama (ytsearchN:) ve oynatma listesi analizi için ortak kullanılır.
        """
        if not ytdlp_exists():
            raise RuntimeError("yt-dlp.exe bulunamadı. Ayarlar sekmesinden indirin/güncelleyin.")
        args = [
            YTDLP_EXE, '--no-warnings', '--flat-playlist',
            '--dump-single-json', '--skip-download',
            '--user-agent', ua, target,
        ]
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=60,
            creationflags=CREATE_NO_WINDOW)
        if proc.returncode != 0:
            raise RuntimeError(
                (proc.stderr or proc.stdout or "yt-dlp bilgi alma hatası").strip()[:300])
        return json.loads(proc.stdout)

    def _do_search(self, query: str):
        """yt-dlp.exe ile YouTube araması yapar — arka plan thread'inde çalışır."""
        try:
            ua = random.choice(USER_AGENTS)
            info = self._ytdlp_dump_json(f"ytsearch15:{query}", ua)
            entries = info.get('entries', []) if info else []
            results = []
            for e in entries:
                title   = e.get('title', '(Başlık yok)')
                channel = e.get('uploader') or e.get('channel', '')
                url     = e.get('webpage_url') or e.get('url', '')
                duration = e.get('duration')
                dur_str = ""
                if duration:
                    m, s = divmod(int(duration), 60)
                    dur_str = f"  [{m}:{s:02d}]"
                label = f"{title}{dur_str}  —  {channel}"
                results.append((label, url, title, channel))
            self._ui_update(self._show_search_results, results)
        except Exception as exc:
            self._ui_update(self._show_search_error, str(exc))

    def _show_search_results(self, results: list):
        self.btn_search.Enable()
        self.search_results.Clear()
        self._search_result_data = results
        if not results:
            self.search_results.Append("Sonuç bulunamadı.")
            self.speaker.say("Arama sonucu bulunamadı.", 0)
            return
        for label, url, title, channel in results:
            self.search_results.Append(label)
        self.speaker.say(f"{len(results)} sonuç bulundu. Listeden seçim yapabilirsiniz.", 0)
        self.search_results.SetFocus()

    def _show_search_error(self, err: str):
        self.btn_search.Enable()
        bildir(self, f"Arama sırasında hata oluştu:\n{err[:200]}", "Hata", wx.ICON_ERROR)
        self.speaker.say("Arama başarısız.", 0)

    def _on_add_from_search(self, event):
        selections = self.search_results.GetSelections()
        if not selections:
            bildir(self, "Lütfen listeden en az bir video seçin.", "Uyarı", wx.ICON_WARNING)
            return
        added_urls = []
        for idx in selections:
            if idx < len(self._search_result_data):
                _, url, title, channel = self._search_result_data[idx]
                if url:
                    added_urls.append(url)
        if not added_urls:
            bildir(self, "Seçilen ögelerde geçerli URL bulunamadı.", "Hata", wx.ICON_ERROR)
            return
        current = self.url_input.GetValue().strip()
        sep = "\n" if current else ""
        self.url_input.SetValue(current + sep + "\n".join(added_urls))
        self.url_input.SetInsertionPointEnd()
        self.speaker.say(f"{len(added_urls)} video URL alanına eklendi. İndirmeyi Başlat butonuna basabilirsiniz.", 0)

    # ─────────────────────────────────────────────────────────────────────────
    # UI GÜNCELLEME YARDIMCILARI (ana thread'den çağrılır)
    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_history(self):
        self.history_list.Clear()
        for item in reversed(self.history):
            self.history_list.Append(item)

    def _refresh_log(self):
        lines = [f"[{e['zaman']}] {e['mesaj']}" for e in reversed(self.log)]
        self.log_text.SetValue("\n".join(lines))

    def _update_row(self, item: DownloadItem, status, prog, speed, eta, attempt=""):
        i = item.idx
        self.queue_list.SetItem(i, 0, item.title[:70])
        self.queue_list.SetItem(i, 1, status)
        self.queue_list.SetItem(i, 2, prog)
        self.queue_list.SetItem(i, 3, speed)
        self.queue_list.SetItem(i, 4, eta)
        self.queue_list.SetItem(i, 5, str(attempt))

    def _log_event(self, message: str):
        entry = {"zaman": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mesaj": message}
        self.log.append(entry)
        self._ui_update(self._refresh_log)

    # ─────────────────────────────────────────────────────────────────────────
    # İNDİRME MOTORU (yt-dlp.exe subprocess ile çalışır)
    # ─────────────────────────────────────────────────────────────────────────
    def _video_format_selector(self, max_height=None) -> str:
        """
        Sağlam bir yt-dlp format seçici üretir. [ext=mp4] gibi katı filtreler
        KULLANMAZ; bunun yerine -S (format sıralama) ile mp4/h264 TERCİH
        edilir ama bulunamazsa vp9/av1/webm otomatik kabul edilir. Zincir her
        zaman koşulsuz bir "/b" (en iyi mevcut format) ile biter; bu sayede
        "Requested format is not available" hatası neredeyse hiç oluşmaz.
        """
        if max_height:
            return (f'bv*[height<={max_height}]+ba/b[height<={max_height}]/'
                     f'bv*+ba/b')
        return 'bv*+ba/b'

    def _build_ytdlp_args(self, fmt: str, attempt: int, outtmpl: str) -> list:
        """
        yt-dlp.exe için komut satırı argümanlarını oluşturur.
        Her denemede farklı User-Agent kullanır (429'a karşı rotasyon).

        NOT: Önceki bir sürümde '--extractor-args youtube:player_client=...'
        ile android/ios/tv_embedded istemcilerine geçiş denenmişti. YouTube
        2025'ten itibaren bu istemcilerde PO-token zorunluluğu getirdiği için
        bu, "Requested format is not available" hatasını AZALTMAK yerine
        ARTIRIYORDU (android/ios istemcisi çoğu video için format listesini
        boşaltıyor). Bu yüzden istemci rotasyonu tamamen kaldırıldı; sadece
        varsayılan (web) istemci kullanılır.
        """
        ffmpeg_path = get_ffmpeg_path()
        ua = USER_AGENTS[attempt % len(USER_AGENTS)]

        args = [
            YTDLP_EXE,
            '--newline', '--no-color', '--no-warnings',
            # '--print' komutu arka planda '--quiet' modunu devreye sokar; bu da
            # ilerleme satırlarını bastırır. '--progress' bunu zorla geri açar,
            # yoksa kuyrukta "Bağlanıyor..." direkt "Tamamlandı"ya atlar.
            '--progress',
            '--no-playlist',        # Her video tek tek indirilir; genişletme biz yaptık
            '--socket-timeout', '30',
            '--retries', '3',
            '--fragment-retries', '5',
            '--extractor-retries', '3',
            '--user-agent', ua,
            '--add-header', 'Accept-Language: tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            '-o', outtmpl,
            # Video başlığını indirme başlamadan hemen önce tek satır olarak yazdır
            '--print', 'before_dl:TITLE::%(title)s',
            # İlerleme ve dönüştürme aşamalarını ayrıştırılabilir satırlar olarak yazdır
            '--progress-template',
            'download:DLPROG::%(progress._percent_str)s::%(progress._speed_str)s::%(progress._eta_str)s',
            '--progress-template', 'postprocess:PPROG::%(progress._percent_str)s',
        ]

        if ffmpeg_path:
            args += ['--ffmpeg-location', ffmpeg_path]

        speed = self.settings.get('speed_limit', '').strip()
        if speed:
            args += ['--limit-rate', speed]

        proxy = self.settings.get('proxy', '').strip()
        if proxy:
            args += ['--proxy', proxy]

        is_thumb_only = 'Sadece Kapak' in fmt

        if is_thumb_only:
            args += ['--skip-download', '--write-thumbnail', '--convert-thumbnails', 'jpg']
            return args

        if 'FLAC' in fmt:
            args += ['-f', 'ba/b', '-x', '--audio-format', 'flac']
        elif 'Opus' in fmt:
            args += ['-f', 'ba/b', '-x', '--audio-format', 'opus']
        elif 'AAC' in fmt or 'M4A' in fmt:
            args += ['-f', 'ba/b', '-x', '--audio-format', 'aac',
                      '--audio-quality', '256K']
        elif 'MP3 320' in fmt:
            args += ['-f', 'ba/b', '-x', '--audio-format', 'mp3',
                      '--audio-quality', '320K']
        elif 'MP3 192' in fmt:
            args += ['-f', 'ba/b', '-x', '--audio-format', 'mp3',
                      '--audio-quality', '192K']
        elif '4K' in fmt or '2160p' in fmt:
            args += ['-f', self._video_format_selector(2160),
                      '-S', 'res,codec:h264:m4a',
                      '--merge-output-format', 'mp4']
        elif '1440p' in fmt:
            args += ['-f', self._video_format_selector(1440),
                      '-S', 'res,codec:h264:m4a',
                      '--merge-output-format', 'mp4']
        elif '1080p' in fmt:
            args += ['-f', self._video_format_selector(1080),
                      '-S', 'res,codec:h264:m4a',
                      '--merge-output-format', 'mp4']
        elif '720p' in fmt:
            args += ['-f', self._video_format_selector(720),
                      '-S', 'res,codec:h264:m4a',
                      '--merge-output-format', 'mp4']
        elif '480p' in fmt:
            args += ['-f', self._video_format_selector(480),
                      '-S', 'res,codec:h264:m4a',
                      '--merge-output-format', 'mp4']
        elif 'WebM' in fmt:
            args += ['-f', self._video_format_selector(),
                      '-S', 'codec:vp9:opus',
                      '--merge-output-format', 'webm']
        else:
            args += ['-f', self._video_format_selector(),
                      '-S', 'res,codec:h264:m4a',
                      '--merge-output-format', 'mp4']

        is_audio_fmt = fmt.startswith('Ses')

        if self.settings.get('embed_meta') and ffmpeg_path:
            args += ['--embed-metadata', '--write-thumbnail', '--embed-thumbnail']

        if self.settings.get('subtitles'):
            langs = self.settings.get('subtitle_langs', 'tr,en').strip() or 'tr,en'
            args += ['--write-subs', '--write-auto-subs', '--sub-langs', langs]
            # YouTube'un altyazı (timedtext/caption) API'si, video akışından
            # AYRI ve çok daha agresif şekilde hız sınırlıyor. Art arda birden
            # fazla dil istendiğinde (veya sık indirmelerde) '429 Too Many
            # Requests' hatası video indirmesinden bağımsız olarak, sadece
            # altyazı isteklerinde tetiklenebiliyor. Her altyazı isteği
            # öncesi kısa bir bekleme koyarak bunu büyük ölçüde önlüyoruz.
            args += ['--sleep-subtitles', '5']
            if not is_audio_fmt and self.settings.get('subtitle_embed', True) and ffmpeg_path:
                args += ['--embed-subs']

        if self.settings.get('sponsorblock') and not is_audio_fmt:
            args += ['--sponsorblock-remove', 'sponsor,selfpromo,interaction']

        return args

    # Bu hatalar alındığında YENİDEN DENEME yapılır (yeni User-Agent ile
    # çoğu zaman çözülür). Gerçekten kalıcı hatalarda (video silinmiş, özel,
    # telif nedeniyle kaldırılmış vb.) yeniden denemek anlamsızdır.
    RETRYABLE_ERROR_PATTERNS = [
        '429', 'too many requests', 'rate limit',
        'requested format is not available',
        'unable to extract',
        'sign in to confirm',
        'http error 403', 'http error 5',
        'nsig extraction failed',
        'failed to extract any player response',
        'unable to download webpage',
        'connection reset', 'timed out', 'temporary failure',
    ]

    def _is_429_error(self, text: str) -> bool:
        msg = text.lower()
        return '429' in msg or 'too many requests' in msg or 'rate limit' in msg

    def _is_retryable_error(self, text: str) -> bool:
        msg = text.lower()
        return any(pat in msg for pat in self.RETRYABLE_ERROR_PATTERNS)

    def _download_single(self, url: str, item: DownloadItem,
                         fmt: str) -> bool:
        """
        yt-dlp.exe'yi subprocess olarak çalıştırıp tek bir URL'yi indirir.
        HTTP 429 ve benzeri geçici hatalarda üstel geri çekilme + farklı
        User-Agent ile yeniden dener.
        Döndürür: True = başarılı, False = başarısız.
        """
        if not ytdlp_exists():
            item.error = "yt-dlp.exe bulunamadı"
            item.status = "Hata"
            self._ui_update(self._update_row, item, "Hata ✗", "0%", "", "", "")
            self._log_event("yt-dlp.exe bulunamadı — Ayarlar sekmesinden indirin/güncelleyin.")
            return False

        self._current_item = item

        # Playlist videolarını alt klasöre ve numaralı olarak kaydet
        user_template = self.settings.get('output_template', '%(title)s.%(ext)s').strip() \
            or '%(title)s.%(ext)s'
        if item.playlist_title:
            safe_pl = re.sub(r'[\\/*?:"<>|]', '_', item.playlist_title)[:60]
            outtmpl = os.path.join(
                self.settings['dir'], safe_pl,
                f"{item.playlist_idx:03d} - {user_template}")
        else:
            outtmpl = os.path.join(self.settings['dir'], user_template)

        for attempt in range(RETRY_MAX):
            if self.stop_event.is_set():
                return False

            attempt_label = f"{attempt + 1}/{RETRY_MAX}"
            self._ui_update(self._update_row, item, "Bağlanıyor...", "0%", "", "", attempt_label)
            self._log_event(f"Deneme {attempt_label}: {url[:60]}")

            args = self._build_ytdlp_args(fmt, attempt, outtmpl) + [url]
            final_title = item.title
            output_lines: list = []

            try:
                proc = subprocess.Popen(
                    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, universal_newlines=True,
                    creationflags=CREATE_NO_WINDOW)
            except Exception as exc:
                item.error = f"yt-dlp.exe çalıştırılamadı: {exc}"
                item.status = "Hata"
                self._ui_update(self._update_row, item, "Hata ✗", item.progress, "", "", attempt_label)
                self._log_event(f"yt-dlp.exe çalıştırma hatası: {exc}")
                return False

            cancelled = False
            for line in proc.stdout:
                line = line.rstrip('\n').strip()
                if not line:
                    continue
                output_lines.append(line)
                if len(output_lines) > 200:
                    output_lines.pop(0)

                if self.stop_event.is_set():
                    cancelled = True
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
                    break

                if line.startswith("TITLE::"):
                    real_title = line[len("TITLE::"):].strip()
                    if real_title:
                        if item.playlist_title and item.playlist_idx is not None:
                            final_title = f"[{item.playlist_idx}/{item.playlist_total}] {real_title[:55]}"
                        else:
                            final_title = real_title
                        item.title = final_title

                elif line.startswith("DLPROG::"):
                    parts = line[len("DLPROG::"):].split("::")
                    p     = parts[0].strip() if len(parts) > 0 else ""
                    speed = parts[1].strip() if len(parts) > 1 else ""
                    eta   = parts[2].strip() if len(parts) > 2 else ""
                    item.progress = p if p else item.progress
                    item.speed    = speed
                    item.status   = "İndiriliyor"
                    try:
                        pct = int(float(p.replace('%', '').strip()))
                    except Exception:
                        pct = 0
                    self._ui_update(self._update_row, item, "İndiriliyor", p, speed, eta, attempt_label)
                    self._ui_update(self.progress_bar.SetValue, pct)
                    self._ui_update(self.speaker.say, f"{item.title[:30]}  {p}  {speed}", 1)

                elif line.startswith("PPROG::"):
                    item.status = "Dönüştürülüyor"
                    self._ui_update(self._update_row, item, "Dönüştürülüyor...", "100%", "", "", attempt_label)
                    self._ui_update(self.progress_bar.SetValue, 100)
                    self._ui_update(self.speaker.say, f"Dönüştürülüyor: {item.title[:40]}", 1)

            if cancelled:
                item.error = "İptal edildi"
                self._ui_update(self._update_row, item, "İptal", item.progress, "", "", attempt_label)
                self._log_event(f"İptal: {url[:60]}")
                return False

            returncode = proc.wait()
            full_output = "\n".join(output_lines)

            if returncode == 0:
                item.title    = final_title
                item.status   = "Tamamlandı"
                item.end_time = datetime.now().isoformat()
                self._ui_update(self._update_row, item, "Tamamlandı ✓", "100%", "", "", attempt_label)
                self._ui_update(self.progress_bar.SetValue, 100)
                self._ui_update(self.speaker.say, f"Tamamlandı: {final_title[:45]}", 0)
                history_entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {final_title}"
                if item.playlist_title:
                    history_entry += f"  (Liste: {item.playlist_title[:40]})"
                self.history.append(history_entry)
                self._ui_update(self._save, HISTORY_FILE, self.history)
                self._ui_update(self._refresh_history)
                self.downloaded_urls.add(url)
                self._ui_update(self._save, DOWNLOADED_URLS_FILE, sorted(self.downloaded_urls))
                self._log_event(f"Tamamlandı: {final_title}")
                return True

            # ── Hata durumu ──────────────────────────────────────────────────
            err_lines = [l for l in output_lines if l.strip()]
            err_str = err_lines[-1] if err_lines else "Bilinmeyen hata"

            if self._is_429_error(full_output):
                sleep_time = RETRY_BASE_SLEEP * (2 ** attempt) + random.uniform(0, RETRY_JITTER_MAX)
                sleep_time = min(sleep_time, 120)
                msg = (f"429 Too Many Requests — Bekleniyor {sleep_time:.0f}s "
                       f"(Deneme {attempt_label})")
                self._ui_update(self._update_row, item, msg, item.progress, "", "", attempt_label)
                self._ui_update(self.speaker.say, msg[:60], 0)
                self._log_event(msg + f" | URL: {url[:50]}")

                deadline = time.time() + sleep_time
                while time.time() < deadline:
                    if self.stop_event.is_set():
                        return False
                    time.sleep(0.5)
                continue

            elif attempt < RETRY_MAX - 1 and self._is_retryable_error(full_output):
                # Geçici olabilecek hata (bot-koruması, ağ zaman aşımı vb.) —
                # kısa bir bekleme sonrası farklı bir User-Agent ile yeniden
                # denenir (istemci/player_client rotasyonu KULLANILMAZ; bkz.
                # _build_ytdlp_args üzerindeki not).
                sleep_time = 4 + random.uniform(0, 4)
                msg = (f"Geçici hata, yeniden deneniyor "
                       f"(Deneme {attempt_label}): {err_str[:70]}")
                self._ui_update(self._update_row, item, "Yeniden deneniyor...", item.progress, "", "", attempt_label)
                self._ui_update(self.speaker.say, msg[:70], 0)
                self._log_event(msg + f" | URL: {url[:50]}")

                deadline = time.time() + sleep_time
                while time.time() < deadline:
                    if self.stop_event.is_set():
                        return False
                    time.sleep(0.3)
                continue

            else:
                item.error = err_str[:120]
                item.status = "Hata"
                self._ui_update(self._update_row, item, "Hata ✗", item.progress, "", "", attempt_label)
                self._log_event(f"Hata: {url[:60]} — {err_str[:100]}")
                self._ui_update(self.speaker.say, f"Hata: {item.title[:35]}", 0)
                return False

        # Tüm denemeler tükendi
        item.error  = f"{RETRY_MAX} denemede başarısız"
        item.status = "Hata"
        self._ui_update(self._update_row, item, "Hata ✗", item.progress, "", "", f"{RETRY_MAX}/{RETRY_MAX}")
        self._log_event(f"Başarısız ({RETRY_MAX} deneme tükendi): {url[:60]}")
        self._ui_update(self.speaker.say, f"Başarısız: {url[:35]}", 0)
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # PLAYLIST GENİŞLETME
    # ─────────────────────────────────────────────────────────────────────────
    def _expand_urls(self, urls: list) -> list:
        """
        URL listesindeki oynatma listelerini tekil video URL'lerine genişletir.
        Normal video URL'leri değişmeden kalır.
        Döndürür: [(url, playlist_title_or_None, video_index_or_None, total_or_None), ...]
        """
        expanded = []
        ua = random.choice(USER_AGENTS)

        for url in urls:
            if self.stop_event.is_set():
                break

            # Playlist mi kontrol et (list= parametresi var mı veya /playlist? yolu)
            is_playlist_url = ('list=' in url and 'watch?v=' not in url) or '/playlist?' in url
            is_playlist_enabled = self.settings.get('playlist', False)

            if is_playlist_url and is_playlist_enabled:
                # Playlist içeriğini düz listele
                self._ui_update(self.speaker.say,
                                f"Oynatma listesi analiz ediliyor: {url[:50]}", 0)
                try:
                    info = self._ytdlp_dump_json(url, ua)

                    if info and info.get('_type') == 'playlist':
                        entries = info.get('entries', [])
                        pl_title = info.get('title', 'Oynatma Listesi')
                        pl_total = len(entries)
                        self._log_event(
                            f"Playlist genişletildi: '{pl_title}' — {pl_total} video")
                        for i, entry in enumerate(entries):
                            if entry is None:
                                continue
                            vid_url = (entry.get('webpage_url') or
                                       entry.get('url') or
                                       f"https://www.youtube.com/watch?v={entry.get('id', '')}")
                            if vid_url:
                                expanded.append((vid_url, pl_title, i + 1, pl_total))
                    else:
                        # extract_flat bazen direkt video döndürür
                        expanded.append((url, None, None, None))
                except Exception as exc:
                    self._log_event(f"Playlist analiz hatası: {url[:50]} — {exc}")
                    # Hata olursa orijinal URL'yi ekle, yt-dlp kendi halleder
                    expanded.append((url, None, None, None))
            else:
                expanded.append((url, None, None, None))

        return expanded

    def _process_downloads(self, urls: list):
        """İndirme kuyruk döngüsü — arka plan thread'inde çalışır."""
        fmt = self.format_choice.GetValue()

        # ── 1. Adım: Playlist'leri genişlet ──────────────────────────────────
        self._ui_update(self.speaker.say,
                        "Adresler kontrol ediliyor, oynatma listeleri analiz ediliyor...", 0)
        expanded = self._expand_urls(urls)

        if self.stop_event.is_set():
            self.active = False
            self._ui_update(self.btn_start.Enable)
            self._ui_update(self.btn_cancel.Disable)
            return

        total     = len(expanded)
        completed = 0
        failed    = 0

        # ── 2. Adım: Kuyruğa ekle ─────────────────────────────────────────────
        self._ui_update(self.queue_list.DeleteAllItems)
        self.download_items.clear()
        time.sleep(0.05)

        for idx, (url, pl_title, pl_idx, pl_total) in enumerate(expanded):
            item = DownloadItem(url, idx)
            # Başlık: playlist ise "PL: Oynatma Listesi  [3/12]" formatında göster
            if pl_title:
                display = f"[{pl_idx}/{pl_total}] {pl_title[:50]}"
            else:
                display = url[:70]
            item.title = display
            item.playlist_title = pl_title
            item.playlist_idx   = pl_idx
            item.playlist_total = pl_total
            self.download_items[url] = item
            self._ui_update(self.queue_list.InsertItem, idx, display)
            self._ui_update(self.queue_list.SetItem, idx, 1, "Kuyrukta")
            self._ui_update(self.queue_list.SetItem, idx, 2, "0%")

        time.sleep(0.15)
        summary_prefix = f"Toplam {total} video" + (
            f" ({len(urls)} adres genişletildi)" if total != len(urls) else "")
        self._ui_update(self.speaker.say, f"{summary_prefix} — indirme başlıyor.", 0)

        # ── 3. Adım: İndirme döngüsü ─────────────────────────────────────────
        for idx, (url, pl_title, pl_idx, pl_total) in enumerate(expanded):
            if self.stop_event.is_set():
                self._log_event("Kullanıcı tarafından durduruldu.")
                break

            item = self.download_items[url]

            success = self._download_single(url, item, fmt)

            if success:
                completed += 1
            else:
                failed += 1
                if not self.stop_event.is_set():
                    self.failed_urls.append(url)

            overall_pct = int((idx + 1) / total * 100)
            self._ui_update(self.progress_bar.SetValue, overall_pct)

            if idx < total - 1 and not self.stop_event.is_set():
                mn = self.settings.get('between_dl_min', BETWEEN_DL_MIN)
                mx = self.settings.get('between_dl_max', BETWEEN_DL_MAX)
                wait = random.uniform(mn, mx)
                self._ui_update(self.speaker.say,
                                f"{idx+1}/{total} tamamlandı. Sonraki için {wait:.1f}s bekleniyor.", 0)
                deadline = time.time() + wait
                while time.time() < deadline:
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.3)

        # ── 4. Adım: Bitti ────────────────────────────────────────────────────
        self.active = False
        self._ui_update(self.btn_start.Enable)
        self._ui_update(self.btn_cancel.Disable)
        if self.failed_urls:
            self._ui_update(self.btn_retry.Enable)

        if self.settings.get('notifications'):
            self._ui_update(wx.Bell)

        summary = (f"İşlem tamamlandı. "
                   f"Başarılı: {completed}, Başarısız: {failed}, Toplam: {total}.")
        self._ui_update(self.speaker.say, summary, 0)
        self._log_event(summary)
        self._save(LOG_FILE, self.log)

        if completed > 0:
            msg = (f"{summary}\n\n"
                   f"Dosyalar şuraya kaydedildi:\n{self.settings['dir']}")
            if failed > 0:
                msg += (f"\n\nBaşarısız {failed} öge için 'Başarısızları Yeniden Dene' "
                        f"butonunu (Ctrl+R) kullanabilirsiniz.")
            self._ui_update(bildir, self, msg, "İndirme Tamamlandı")
        elif failed > 0:
            self._ui_update(
                bildir, self,
                f"Tüm indirmeler başarısız oldu ({failed} öge).\n"
                f"Ctrl+R ile yeniden deneyebilirsiniz.",
                "Hata", wx.ICON_WARNING)


# ── Uygulama başlangıcı ───────────────────────────────────────────────────────
if __name__ == "__main__":
    app = wx.App(False)
    frame = YouTubeDownloader()
    app.MainLoop()
