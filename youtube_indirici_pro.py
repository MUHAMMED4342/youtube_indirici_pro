"""
YouTube İndirici Pro
Görme Engelli Erişilebilirlik (NVDA / JAWS / Narrator) ile tam uyumlu.

Özellikler:
  • Altyazı indirme (otomatik/manuel, gömme veya ayrı .srt)
  • SponsorBlock — sponsor/tanıtım bölümlerini otomatik atlama
  • Proxy desteği
  • Özelleştirilebilir dosya adı şablonu
  • 4K (2160p) / 1440p ve Opus/AAC ses biçimleri
  • .txt dosyasından toplu URL içe aktarma
  • Kuyruktan öge silme / tamamlananları temizleme
  • Sistem tepsisine küçültme (isteğe bağlı)
  • Video/oynatma listesi önizleme (süre, kanal, tarih) arama sonuçlarında

Bu dosya arayüz (wx.Frame) kodunu içerir. İndirme motoru mantığı
download_engine.py içindedir (DownloadEngineMixin); ortak sabitler ve
yardımcı fonksiyonlar ytdlp_core.py içindedir.
"""

import wx
try:
    import wx.adv
    HAS_WX_ADV = True
except Exception:
    HAS_WX_ADV = False
import threading
import queue
import os
import subprocess
import random
from datetime import datetime

from ytdlp_core import (
    SETTINGS_FILE, HISTORY_FILE, LOG_FILE, DOWNLOADED_URLS_FILE,
    KISAYOLLAR, USER_AGENTS, RETRY_MAX, BETWEEN_DL_MIN, BETWEEN_DL_MAX,
    CREATE_NO_WINDOW, YTDLP_EXE,
    PARALLEL_DOWNLOADS_DEFAULT, PARALLEL_DOWNLOADS_MAX,
    LOG_MAX_ENTRIES, HISTORY_MAX_ENTRIES,
    ytdlp_exists, download_ytdlp_exe, get_ytdlp_version,
    get_ffmpeg_status_message,
    is_valid_url, normalize_urls, bildir,
    safe_load_json, safe_save_json,
    validate_output_template, validate_speed_limit, validate_proxy,
    StatusSpeaker, DownloadItem, YtdlpDogrulamaHatasi,
)
from download_engine import DownloadEngineMixin


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
class YouTubeDownloader(wx.Frame, DownloadEngineMixin):

    def __init__(self):
        super().__init__(
            parent=None,
            title="YouTube İndirici Pro",
            size=(740, 680),
        )

        self.settings, ayar_bozuk = self._load(SETTINGS_FILE, {
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
            # Aynı anda kaç video indirileceği. Varsayılan ve ÖNERİLEN değer
            # 1'dir (kapalı / sırayla). Daha yüksek bir değer indirmeleri
            # hızlandırabilir ama YouTube'un hız sınırlamasına (çok sayıda
            # video "Bekliyor" durumuna düşmesi) yakalanma ihtimalini artırır.
            'parallel_downloads': PARALLEL_DOWNLOADS_DEFAULT,
        }, return_status=True)
        self.history, gecmis_bozuk = self._load(HISTORY_FILE, [], return_status=True)
        self.log, gunluk_bozuk     = self._load(LOG_FILE, [], return_status=True)
        indirilenler, adres_bozuk  = self._load(DOWNLOADED_URLS_FILE, [], return_status=True)
        self.downloaded_urls: set = set(indirilenler)
        self._bozuk_dosya_uyarisi = ayar_bozuk or gecmis_bozuk or gunluk_bozuk or adres_bozuk
        # NOT: Anahtar olarak URL DEĞİL, kuyruktaki sıra numarası (idx)
        # kullanılır. Aynı video adresi iki farklı oynatma listesinde (veya
        # aynı kuyrukta) birden fazla kez geçebilir; URL'yi anahtar yapmak bu
        # durumda ögelerin kuyrukta birbirinin üzerine yazılmasına yol açar.
        self.download_items: dict[int, DownloadItem] = {}
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

        if getattr(self, '_bozuk_dosya_uyarisi', False):
            bildir(
                self,
                "Bazı kayıtlı dosyalarınız (ayarlar, geçmiş veya günlük) okunamadığı için "
                "sıfırdan başlatıldı. Eski hâlleri güvenlik amacıyla klasörünüzde "
                "\".bozuk-\" ile başlayan bir yedek olarak saklandı, silinmedi.",
                "Bilgi", wx.ICON_INFORMATION)

        if self.settings.get('auto_clipboard'):
            self._clipboard_timer.Start(1500)

        # İlk çalıştırmada indirme motoru yoksa arka planda otomatik kur
        self._ytdlp_busy = False
        if not ytdlp_exists():
            self.speaker.say("Program ilk kez hazırlanıyor, lütfen bekleyin...", 0)
            self._ytdlp_busy = True
            threading.Thread(target=self._auto_download_ytdlp_on_start, daemon=True).start()

    # ── JSON yardımcıları ─────────────────────────────────────────────────────
    def _load(self, path, default, return_status: bool = False):
        """
        Bir ayar/geçmiş/günlük dosyasını okur. Dosya bozuksa sessizce
        sıfırlamak yerine bozuk dosyayı ".bozuk-<zaman>" olarak yedekler ve
        (varsa) bunu çağırana bildirir — bkz. safe_load_json.
        """
        bozuldu, veri = safe_load_json(path, default)
        if return_status:
            return veri, bozuldu
        return veri

    def _save(self, path, data):
        # Geçmiş / günlük listelerinin sınırsız büyümesini önlemek için
        # kaydetmeden hemen önce en eski kayıtları buda.
        if path == HISTORY_FILE and isinstance(data, list) and len(data) > HISTORY_MAX_ENTRIES:
            data = data[-HISTORY_MAX_ENTRIES:]
        elif path == LOG_FILE and isinstance(data, list) and len(data) > LOG_MAX_ENTRIES:
            data = data[-LOG_MAX_ENTRIES:]
        if not safe_save_json(path, data):
            self._log_event(f"Kayıt hatası: {path} dosyasına yazılamadı.")

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

        lbl_results = wx.StaticText(panel, label="Arama Sonuçları (kanal, süre, izlenme ve varsa beğeni/tarih ile birlikte):")
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
        sizer.Add(wx.StaticText(panel, label="İndirmeler Arası Bekleme Süresi (YouTube'un sizi geçici olarak engellemesini önler):"),
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

        # Aynı anda indirme (paralel indirme) — varsayılan ve önerilen: kapalı (1)
        sizer.Add(wx.StaticText(panel, label="Aynı Anda İndirilecek Video Sayısı:"),
                  0, wx.LEFT | wx.TOP, 10)
        hbox_parallel = wx.BoxSizer(wx.HORIZONTAL)
        self.spin_parallel = wx.SpinCtrl(
            panel, min=1, max=PARALLEL_DOWNLOADS_MAX,
            value=str(self.settings.get('parallel_downloads', PARALLEL_DOWNLOADS_DEFAULT)),
            name="Aynı anda indirme sayısı")
        self.spin_parallel.SetToolTip(
            "Önerilen: 1 (kapalı / sırayla indirme). Bunu artırmak indirmeleri hızlandırabilir, "
            "ama YouTube'un sizi geçici olarak engellemesi ihtimalini de artırır. "
            "Sorun yaşarsanız 1'e geri döndürün.")
        hbox_parallel.Add(self.spin_parallel, 0, wx.RIGHT, 10)
        hbox_parallel.Add(
            wx.StaticText(panel, label="(Önerilen: 1 — çoğu kullanıcı bu ayarı değiştirmemelidir)"),
            0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(hbox_parallel, 0, wx.ALL, 10)

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

        ffmpeg_ok, ff_msg = get_ffmpeg_status_message()
        lbl_ff = wx.StaticText(panel, label=ff_msg, name="Video birleştirme bileşeni durum etiketi")
        if not ffmpeg_ok:
            lbl_ff.SetForegroundColour(wx.Colour(180, 90, 0))
        sizer.Add(lbl_ff, 0, wx.ALL, 10)

        # ── İndirme motoru: durum ve güncelleme ─────────────────────────────
        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(wx.StaticText(panel, label=""), 0, wx.BOTTOM, 2)

        self.lbl_ytdlp = wx.StaticText(
            panel, label=self._ytdlp_status_text(), name="İndirme motoru durum etiketi")
        self.lbl_ytdlp.SetToolTip(
            "Videoları indirmek için kullanılan arka plan bileşeninin durumu ve sürümü.")
        sizer.Add(self.lbl_ytdlp, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        self.btn_update_ytdlp = wx.Button(
            panel, label="İndirme Motorunu Güncelle",
            name="İndirme motorunu güncelle butonu")
        self.btn_update_ytdlp.SetToolTip(
            "Videoları indirmek için kullanılan bileşeni resmi kaynağından en güncel "
            "sürüme günceller. Hiç indirilmemişse önce indirir. Dosyanın bütünlüğü "
            "otomatik olarak doğrulanır.")
        self.btn_update_ytdlp.Bind(wx.EVT_BUTTON, self._on_update_ytdlp)
        sizer.Add(self.btn_update_ytdlp, 0, wx.ALL | wx.EXPAND, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Ayarlar  Alt+2")

    def _ytdlp_status_text(self) -> str:
        if not ytdlp_exists():
            return "İndirme motoru: kurulu değil — aşağıdaki butonla indirebilirsiniz."
        ver = get_ytdlp_version()
        return f"İndirme motoru sürümü: {ver or 'bilinmiyor'} — hazır."

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
        try:
            if wx.TheClipboard.Open():
                try:
                    data = wx.TextDataObject()
                    if wx.TheClipboard.GetData(data):
                        text = data.GetText().strip()
                        current = self.url_input.GetValue().strip()
                        sep = "\n" if current else ""
                        self.url_input.SetValue(current + sep + text)
                        self.url_input.SetInsertionPointEnd()
                        self.speaker.say("Pano içeriği URL alanına yapıştırıldı.")
                finally:
                    wx.TheClipboard.Close()
        except Exception:
            pass

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
        status = self.queue_list.GetItemText(idx, 1)
        if self.active and status in ("İndiriliyor", "Bağlanıyor...", "Dönüştürülüyor"):
            bildir(self, "Şu anda indirilmekte olan öge kuyruktan silinemez.",
                   "Uyarı", wx.ICON_WARNING)
            return
        item_id = self.queue_list.GetItemData(idx)
        for item in self.download_items.values():
            if item.item_id == item_id:
                item.removed = True
                break
        removed_title = self.queue_list.GetItemText(idx, 0)
        self.queue_list.DeleteItem(idx)
        self.speaker.say(f"Kuyruktan silindi: {removed_title[:40]}", 0)

    def _on_clear_completed(self, event):
        removed = 0
        for i in range(self.queue_list.GetItemCount() - 1, -1, -1):
            status = self.queue_list.GetItemText(i, 1)
            if "Tamamlandı" in status:
                self.queue_list.DeleteItem(i)
                removed += 1
        self.speaker.say(f"{removed} tamamlanmış öge kuyruktan temizlendi.", 0)

    def _on_start(self, event):
        if not ytdlp_exists():
            bildir(self,
                   "İndirme motoru henüz hazır değil.\n"
                   "Ayarlar sekmesinden \"İndirme Motorunu Güncelle\" butonuna basın.",
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
        self.speaker.say("İptal sinyali gönderildi. İndirme durduruluyor.", 0)

    def _on_save_settings(self, event):
        mn = self.spin_min.GetValue()
        mx = self.spin_max.GetValue()
        if mn > mx:
            bildir(self, "Minimum bekleme süresi, maksimum değerden büyük olamaz.", "Hata", wx.ICON_ERROR)
            return

        template = self.txt_template.GetValue().strip() or '%(title)s.%(ext)s'
        gecerli, hata = validate_output_template(template)
        if not gecerli:
            bildir(self, f"Dosya adı şablonu geçersiz:\n{hata}", "Hata", wx.ICON_ERROR)
            self.txt_template.SetFocus()
            return

        speed = self.txt_speed.GetValue().strip()
        gecerli, hata = validate_speed_limit(speed)
        if not gecerli:
            bildir(self, hata, "Hata", wx.ICON_ERROR)
            self.txt_speed.SetFocus()
            return

        proxy = self.txt_proxy.GetValue().strip()
        gecerli, hata = validate_proxy(proxy)
        if not gecerli:
            bildir(self, hata, "Hata", wx.ICON_ERROR)
            self.txt_proxy.SetFocus()
            return

        self.settings.update({
            'dir':                self.dir_picker.GetPath(),
            'playlist':           self.chk_playlist.GetValue(),
            'embed_meta':         self.chk_meta.GetValue(),
            'speed_limit':        speed,
            'notifications':      self.chk_notifications.GetValue(),
            'auto_clipboard':     self.chk_auto_clipboard.GetValue(),
            'between_dl_min':     mn,
            'between_dl_max':     mx,
            'subtitles':          self.chk_subtitles.GetValue(),
            'subtitle_langs':     self.txt_sub_langs.GetValue().strip() or 'tr,en',
            'subtitle_embed':     self.chk_sub_embed.GetValue(),
            'sponsorblock':       self.chk_sponsorblock.GetValue(),
            'proxy':              proxy,
            'output_template':    template,
            'skip_duplicates':    self.chk_skip_dup.GetValue(),
            'minimize_tray':      self.chk_minimize_tray.GetValue() if self.chk_minimize_tray else False,
            'parallel_downloads': self.spin_parallel.GetValue(),
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
        last_spoke = getattr(self, '_last_ytdlp_spoke_pct', -1)
        if pct is None or (pct != last_spoke and (pct % 10 == 0 or pct in (0, 100))):
            self._last_ytdlp_spoke_pct = pct
            self.speaker.say(msg, 0)

    def _close_ytdlp_progress_dialog(self):
        dlg = getattr(self, '_ytdlp_progress_dlg', None)
        if dlg:
            dlg.close_dialog()
            self._ytdlp_progress_dlg = None

    def _auto_download_ytdlp_on_start(self):
        """Program ilk açıldığında indirme motoru bulunamazsa, klasik indirme
        penceresiyle otomatik olarak kurar."""
        self._ui_update(self._ui_open_ytdlp_dialog, "İndirme Motoru Hazırlanıyor")
        try:
            download_ytdlp_exe(progress_cb=self._ytdlp_progress_cb)
            self._log_event("İndirme motoru ilk çalıştırmada otomatik olarak kuruldu.")
            self._ui_update(self.speaker.say, "Her şey hazır. İndirmeye başlayabilirsiniz.", 0)
        except YtdlpDogrulamaHatasi as exc:
            self._log_event(f"İndirme motoru bütünlük doğrulaması başarısız: {exc}")
            self._ui_update(
                self.speaker.say,
                "İndirilen dosya güvenlik denetiminden geçemedi ve silindi. "
                "Ayarlar sekmesinden tekrar deneyebilirsiniz.", 0)
        except Exception as exc:
            self._log_event(f"İndirme motoru otomatik kurulum hatası: {exc}")
            self._ui_update(
                self.speaker.say,
                "İndirme motoru otomatik kurulamadı. Ayarlar sekmesinden tekrar deneyebilirsiniz.", 0)
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
        if self.active:
            bildir(self, "İndirme devam ederken indirme motoru güncellenemez. Lütfen önce indirmelerin bitmesini bekleyin veya iptal edin.", "Uyarı", wx.ICON_WARNING)
            return
        if self._ytdlp_busy:
            bildir(self, "İndirme motoru şu anda kuruluyor/güncelleniyor, lütfen bekleyin.", "Bilgi")
            return
        self._ytdlp_busy = True
        self.btn_update_ytdlp.Disable()

        # Klasik indirme penceresini aç; indirme bitene kadar açık kalır
        self._ytdlp_progress_dlg = self._open_ytdlp_progress_dialog("İndirme Motoru Güncelleniyor")
        self._ytdlp_progress_dlg.set_progress(None, "Bağlanılıyor...")
        self.speaker.say("İndirme motoru güncelleniyor / kuruluyor...", 0)
        threading.Thread(target=self._update_ytdlp_worker, daemon=True).start()

    def _update_ytdlp_worker(self):
        try:
            if not ytdlp_exists():
                self._ui_update(self._apply_ytdlp_progress, None, "İndirme motoru kuruluyor...")
                download_ytdlp_exe(progress_cb=self._ytdlp_progress_cb)
                self._log_event("İndirme motoru ilk kez kuruldu.")
                result_msg = "İndirme motoru başarıyla kuruldu."
            else:
                # Önce dahili güncelleme mekanizmasını dene
                self._ui_update(self._apply_ytdlp_progress, None, "Güncelleme kontrol ediliyor...")
                proc = subprocess.run(
                    [YTDLP_EXE, '-U'], capture_output=True, text=True,
                    timeout=90, creationflags=CREATE_NO_WINDOW)
                out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
                self._log_event(f"İndirme motoru güncelleme çıktısı: {out[:200]}")
                if proc.returncode == 0:
                    result_msg = "İndirme motoru zaten güncel." if not out else out
                    self._ui_update(self._apply_ytdlp_progress, 100, "Güncel.")
                else:
                    # Dahili güncelleme bazı durumlarda çalışmayabilir; yeniden indir
                    self._ui_update(
                        self._apply_ytdlp_progress, None,
                        "Dahili güncelleme başarısız, yeniden kuruluyor...")
                    download_ytdlp_exe(progress_cb=self._ytdlp_progress_cb)
                    self._log_event("İndirme motoru yeniden indirilerek güncellendi.")
                    result_msg = "İndirme motoru yeniden indirilerek güncellendi."

            ver = get_ytdlp_version()
            self._ui_update(
                self._on_ytdlp_update_done, True,
                f"{result_msg}\n\nGüncel sürüm: {ver or 'bilinmiyor'}")
        except YtdlpDogrulamaHatasi as exc:
            self._log_event(f"İndirme motoru bütünlük doğrulaması başarısız: {exc}")
            self._ui_update(self._on_ytdlp_update_done, False, str(exc))
        except Exception as exc:
            self._log_event(f"İndirme motoru güncelleme hatası: {exc}")
            self._ui_update(self._on_ytdlp_update_done, False, str(exc))

    def _on_ytdlp_update_done(self, success: bool, msg: str):
        self._ytdlp_busy = False
        self.btn_update_ytdlp.Enable()
        self._refresh_ytdlp_label()
        self._close_ytdlp_progress_dialog()
        if success:
            self.speaker.say("Güncelleme tamamlandı.", 0)
            bildir(self, msg, "Güncelleme Tamamlandı")
        else:
            self.speaker.say("Güncelleme işlemi başarısız oldu.", 0)
            bildir(self, f"Güncelleme sırasında bir sorun oluştu:\n{msg[:300]}", "Hata", wx.ICON_ERROR)

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
        home = os.environ.get('USERPROFILE', os.path.expanduser('~'))
        desktop = os.path.join(home, 'Desktop')
        if not os.path.exists(desktop):
            onedrive_desktop = os.path.join(home, 'OneDrive', 'Desktop')
            if os.path.exists(onedrive_desktop):
                desktop = onedrive_desktop
            else:
                try:
                    os.makedirs(desktop, exist_ok=True)
                except Exception:
                    desktop = home
        fname = f"youtube_indirici_gunluk_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        fpath = os.path.join(desktop, fname)
        try:
            lines = [f"[{e['zaman']}] {e['mesaj']}" for e in list(self.log)]
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
            "PRO ÖZELLİKLER",
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
        if key == wx.WXK_DELETE:
            self._on_remove_selected(event)
            return
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
            if hasattr(self, '_current_proc') and self._current_proc:
                try:
                    terminate_process_tree(self._current_proc)
                except Exception:
                    pass
        self._clipboard_timer.Stop()
        self._ui_timer.Stop()
        if self.tray_icon:
            self.tray_icon.RemoveIcon()
            self.tray_icon.Destroy()
        self.Destroy()

    def _check_clipboard(self, event):
        if self.active:
            return
        try:
            if wx.TheClipboard.Open():
                try:
                    data = wx.TextDataObject()
                    if wx.TheClipboard.GetData(data):
                        text = data.GetText().strip()
                        if is_valid_url(text) and text not in self.url_input.GetValue():
                            current = self.url_input.GetValue().strip()
                            sep = "\n" if current else ""
                            self.url_input.SetValue(current + sep + text)
                            self.url_input.SetInsertionPointEnd()
                            self.speaker.say("YouTube adresi otomatik yapıştırıldı.", 0)
                finally:
                    wx.TheClipboard.Close()
        except Exception:
            pass

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

    @staticmethod
    def _fmt_compact_number(n):
        """1234 -> '1,2 B', 2500000 -> '2,5 Mn'. Yoksa None döner."""
        if n is None:
            return None
        try:
            n = int(n)
        except (TypeError, ValueError):
            return None
        if n >= 1_000_000:
            val = f"{n / 1_000_000:.1f}".replace('.', ',').replace(',0', '')
            return f"{val} Mn"
        if n >= 1_000:
            val = f"{n / 1_000:.1f}".replace('.', ',').replace(',0', '')
            return f"{val} B"
        return str(n)

    @staticmethod
    def _fmt_upload_date(date_str):
        """'20240315' -> '15.03.2024'. Geçersiz/eksikse None döner."""
        if not date_str or len(date_str) != 8 or not date_str.isdigit():
            return None
        return f"{date_str[6:8]}.{date_str[4:6]}.{date_str[0:4]}"

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
                if url and not url.startswith('http'):
                    url = f"https://www.youtube.com/watch?v={url}"
                elif not url and e.get('id'):
                    url = f"https://www.youtube.com/watch?v={e.get('id')}"
                duration = e.get('duration')
                dur_str = ""
                if duration:
                    m, s = divmod(int(duration), 60)
                    dur_str = f"  [{m}:{s:02d}]"

                views_str = self._fmt_compact_number(e.get('view_count'))
                likes_str = self._fmt_compact_number(e.get('like_count'))
                date_str  = self._fmt_upload_date(e.get('upload_date'))

                # Arama sonuçları (--flat-playlist) çoğu videoda kanal,
                # süre ve izlenme sayısını doğrudan verir. Beğeni sayısı ve
                # tam tarih ise YouTube'un arama listeleme sayfasında hiç
                # yer almaz (yalnızca videonun kendi sayfasında bulunur);
                # bu yüzden yt-dlp bazı videolarda bu alanları None
                # döndürebilir — bu durumda ilgili bilgi etikette atlanır.
                meta_bits = []
                if channel:
                    meta_bits.append(channel)
                if views_str:
                    meta_bits.append(f"{views_str} izlenme")
                if likes_str:
                    meta_bits.append(f"{likes_str} beğeni")
                if date_str:
                    meta_bits.append(date_str)

                label = f"{title}{dur_str}"
                if meta_bits:
                    label += "  —  " + "  •  ".join(meta_bits)

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

    def _insert_queue_item(self, idx, item: DownloadItem, display: str):
        self.queue_list.InsertItem(idx, display)
        self.queue_list.SetItemData(idx, item.item_id)
        self.queue_list.SetItem(idx, 1, "Kuyrukta")
        self.queue_list.SetItem(idx, 2, "0%")

    def _update_row(self, item: DownloadItem, status, prog, speed, eta, attempt=""):
        row = self.queue_list.FindItem(-1, item.item_id)
        if row == wx.NOT_FOUND:
            if item.idx < self.queue_list.GetItemCount() and self.queue_list.GetItemData(item.idx) == item.item_id:
                row = item.idx
            else:
                return
        self.queue_list.SetItem(row, 0, item.title[:70])
        self.queue_list.SetItem(row, 1, status)
        self.queue_list.SetItem(row, 2, prog)
        self.queue_list.SetItem(row, 3, speed)
        self.queue_list.SetItem(row, 4, eta)
        self.queue_list.SetItem(row, 5, str(attempt))

    def _log_event(self, message: str):
        entry = {"zaman": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mesaj": message}
        self.log.append(entry)
        self._ui_update(self._refresh_log)



# ── Uygulama başlangıcı ───────────────────────────────────────────────────────
if __name__ == "__main__":
    app = wx.App(False)
    frame = YouTubeDownloader()
    app.MainLoop()
