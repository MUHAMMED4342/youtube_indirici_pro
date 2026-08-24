"""
YouTube İndirici Pro — İndirme Motoru
Görme Engelli Erişilebilirlik (NVDA / JAWS / Narrator) ile tam uyumlu.

Bu modül, yt-dlp.exe'yi subprocess olarak yöneten indirme motorunu içerir:
  • yt-dlp komut satırı argümanlarının oluşturulması (_build_ytdlp_args)
  • Tekil video indirme + otomatik yeniden deneme (_download_single)
  • Oynatma listesi genişletme (_expand_urls)
  • Kuyruk işleme döngüsü (_process_downloads)

DownloadEngineMixin, ana pencere sınıfı (YouTubeDownloader) tarafından
miras alınır; self.settings, self.speaker, self.queue_list gibi UI
durumuna doğrudan erişir. Bu sayede indirme mantığı, arayüz kodundan ayrı
bir dosyada tutulur ama davranış aynı kalır.
"""

import os
import re
import json
import time
import random
import threading
import subprocess
import wx
from datetime import datetime

from ytdlp_core import (
    YTDLP_EXE, USER_AGENTS, CREATE_NO_WINDOW,
    RETRY_MAX, RETRY_BASE_SLEEP, RETRY_JITTER_MAX,
    BETWEEN_DL_MIN, BETWEEN_DL_MAX,
    ytdlp_exists, get_ffmpeg_path, bildir,
    cleanup_temp_files, terminate_process_tree,
    DownloadItem, HISTORY_FILE, DOWNLOADED_URLS_FILE, LOG_FILE,
)


class DownloadEngineMixin:
    """yt-dlp indirme motoru — YouTubeDownloader(wx.Frame) tarafından miras alınır."""

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
            # NOT: '--write-thumbnail' KASITLI OLARAK eklenmiyor. O bayrak
            # yt-dlp'ye kapak resmini videodan BAĞIMSIZ, kalıcı bir dosya
            # olarak diske yazmasını söyler (ör. "Video Adı.webp") ve bu
            # dosya video başarıyla indirilse bile SİLİNMEZ. Sadece
            # '--embed-thumbnail' verildiğinde yt-dlp kapak resmini geçici
            # olarak indirir, videoya gömer ve geçici dosyayı otomatik siler.
            args += ['--embed-metadata', '--embed-thumbnail']

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

    def _is_429_error(self, text: str) -> bool:
        msg = text.lower()
        return '429' in msg or 'too many requests' in msg or 'rate limit' in msg

    def _is_retryable_error(self, text: str) -> bool:
        msg = text.lower()
        return any(pat in msg for pat in self.RETRYABLE_ERROR_PATTERNS)

    def _download_single(self, url: str, item: DownloadItem, fmt: str):
        """
        yt-dlp.exe'yi subprocess olarak çalıştırıp tek bir URL'yi indirir.
        HTTP 429 ve benzeri geçici hatalarda üstel geri çekilme + farklı
        User-Agent ile yeniden dener.

        Döndürür:
          True      = başarılı, bu çağrı SENKRON olarak tamamlandı.
          False     = başarısız, bu çağrı SENKRON olarak tamamlandı.
          "PENDING" = ham indirme verisi bitti; dönüştürme/birleştirme
                      (ffmpeg postprocess) arka planda bir thread'de
                      sürüyor. _process_downloads BEKLEMEDEN bir sonraki
                      videoya geçebilir. Nihai sonuç (başarılı/başarısız)
                      arka plan thread'i bittiğinde self._on_item_done(...)
                      çağrısıyla bildirilir.
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

        # Bu indirmeye ait geçici dosyaların aranacağı klasör (temizlik için)
        target_dir = os.path.dirname(outtmpl) or self.settings['dir']

        return self._run_attempts(url, item, fmt, outtmpl, target_dir, start_attempt=0)

    def _run_attempts(self, url: str, item: DownloadItem, fmt: str,
                       outtmpl: str, target_dir: str, start_attempt: int):
        """
        RETRY_MAX'a kadar deneme yapar (start_attempt'ten başlayarak — bu,
        arka planda devam eden bir denemenin başarısız olup yeniden
        denenmesi gerektiğinde de kullanılır). Bir denemede dönüştürme
        (postprocess/ffmpeg) aşaması başladığı anda ve o an başka bir video
        dönüştürülmüyorsa, kalan bekleme arka plan thread'ine devredilir ve
        bu fonksiyon "PENDING" döner — böylece kuyruk BEKLEMEDEN bir
        sonraki videoyu indirmeye başlayabilir.
        """
        for attempt in range(start_attempt, RETRY_MAX):
            if self.stop_event.is_set():
                return False

            attempt_label = f"{attempt + 1}/{RETRY_MAX}"
            self._ui_update(self._update_row, item, "Bağlanıyor...", "0%", "", "", attempt_label)
            self._log_event(f"Deneme {attempt_label}: {url[:60]}")

            args = self._build_ytdlp_args(fmt, attempt, outtmpl) + [url]
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

            phase = self._read_download_phase(
                proc, item, url, fmt, outtmpl, target_dir, attempt,
                output_lines, attempt_label)

            if phase == "HANDOFF":
                # İndirme verisi tamamlandı; dönüştürme arka planda
                # sürüyor. Sonuç oraya devredildi.
                return "PENDING"

            if phase == "CANCELLED":
                self._handle_cancelled(item, url, target_dir, attempt_label)
                return False

            # phase == "FINISHED": süreç kendiliğinden bitti (ör. sadece
            # kapak resmi indirme gibi dönüştürme adımı olmayan işler,
            # ya da zaten arka planda devam eden bir denemenin sonu).
            result = self._finish_attempt(
                proc, item, url, attempt, attempt_label, target_dir, output_lines)
            if result == "RETRY":
                continue
            return result

        # Tüm denemeler tükendi (ör. arka arkaya 429 hatası)
        item.error  = f"{RETRY_MAX} denemede başarısız"
        item.status = "Hata"
        self._ui_update(self._update_row, item, "Hata ✗", item.progress, "", "", f"{RETRY_MAX}/{RETRY_MAX}")
        self._log_event(f"Başarısız ({RETRY_MAX} deneme tükendi): {url[:60]}")
        self._ui_update(self.speaker.say, f"Başarısız: {url[:35]}", 0)
        removed = cleanup_temp_files(target_dir)
        if removed:
            self._log_event(f"Geçici dosyalar silindi ({len(removed)} adet): {target_dir}")
        return False

    def _read_download_phase(self, proc, item: DownloadItem, url: str, fmt: str,
                              outtmpl: str, target_dir: str, attempt: int,
                              output_lines: list, attempt_label: str) -> str:
        """
        yt-dlp çıktısını satır satır okur (ham indirme ilerlemesi + varsa
        dönüştürme ilerlemesi). Üç şekilde biter:

          "FINISHED"  — alt süreç kendiliğinden bitti; çağıran taraf
                        proc.wait() ile sonucu değerlendirmeli.
          "HANDOFF"   — dönüştürme (ffmpeg) aşaması başladı VE dönüştürme
                        için boş bir "yuva" bulundu (self._pp_semaphore);
                        kalan bekleme ayrı bir thread'e devredildi, bu
                        fonksiyon HEMEN döner. Aynı anda en fazla bir video
                        dönüştürülür, böylece zayıf bilgisayarlarda ffmpeg
                        süreçleri üst üste yığılmaz — yalnızca "şu an inen
                        video" ile "şu an dönüştürülen video" aynı anda
                        çalışır. Yuva doluysa (başka bir video zaten
                        dönüştürülüyorsa) devretme yapılmaz, okumaya normal
                        şekilde devam edilir.
          "CANCELLED" — kullanıcı iptal etti.
        """
        for line in proc.stdout:
            line = line.rstrip('\n').strip()
            if not line:
                continue
            output_lines.append(line)
            if len(output_lines) > 200:
                output_lines.pop(0)

            if self.stop_event.is_set():
                # Sadece yt-dlp.exe'yi değil, onun başlattığı ffmpeg.exe alt
                # sürecini de birlikte kapatır; aksi halde ffmpeg arka
                # planda çalışmaya devam edip çıktı dosyasını kilitli
                # tutabilir ve geçici dosyalar silinemez.
                terminate_process_tree(proc)
                return "CANCELLED"

            if line.startswith("TITLE::"):
                real_title = line[len("TITLE::"):].strip()
                if real_title:
                    if item.playlist_title and item.playlist_idx is not None:
                        item.title = f"[{item.playlist_idx}/{item.playlist_total}] {real_title[:55]}"
                    else:
                        item.title = real_title

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

                if self._pp_semaphore.acquire(blocking=False):
                    self._spawn_background_finish(
                        proc, item, url, fmt, outtmpl, target_dir, attempt,
                        output_lines, attempt_label)
                    return "HANDOFF"
                # Yuva doluysa (zaten başka bir video dönüştürülüyorsa) eski
                # davranışa dön: bu videonun bitmesini burada bekle.

        return "FINISHED"

    def _spawn_background_finish(self, proc, item: DownloadItem, url: str, fmt: str,
                                  outtmpl: str, target_dir: str, attempt: int,
                                  output_lines: list, attempt_label: str):
        """
        Dönüştürme (ffmpeg) aşamasının bitmesini ana kuyruk thread'i yerine
        ayrı bir arka plan thread'inde bekler. Bu sayede ana kuyruk döngüsü
        hemen bir sonraki videonun indirmesine başlayabilir. Sonuç
        (başarılı/başarısız — gerekirse yeniden deneme dahil) bittiğinde
        self._on_item_done(url, item, success) ile bildirilir.
        """
        def worker():
            try:
                phase = self._read_download_phase(
                    proc, item, url, fmt, outtmpl, target_dir, attempt,
                    output_lines, attempt_label)

                if phase == "CANCELLED":
                    self._handle_cancelled(item, url, target_dir, attempt_label)
                    self._on_item_done(url, item, False)
                    return

                result = self._finish_attempt(
                    proc, item, url, attempt, attempt_label, target_dir, output_lines)
                if result == "RETRY":
                    # Dönüştürme sırasında/sonrasında geçici bir hata
                    # çıktıysa, kalan denemeleri burada (arka planda) devam
                    # ettiriyoruz. self._pp_semaphore hâlâ bu thread'de
                    # tutulduğu için yeni bir deneme tekrar PPROG'a ulaşsa
                    # bile ikinci bir devretme yapılmaz; her şey bu thread
                    # içinde senkron sürer.
                    result = self._run_attempts(
                        url, item, fmt, outtmpl, target_dir, start_attempt=attempt + 1)
                if result == "PENDING":
                    # Teorik olarak oluşmaz (yuva zaten dolu) ama güvenlik
                    # amacıyla burada bırakılıyor.
                    return
                self._on_item_done(url, item, bool(result))
            finally:
                self._pp_semaphore.release()

        t = threading.Thread(target=worker, daemon=True)
        self._pending_threads.append(t)
        t.start()

    def _finish_attempt(self, proc, item: DownloadItem, url: str, attempt: int,
                         attempt_label: str, target_dir: str, output_lines: list):
        """
        Alt sürecin (proc) bitmesini bekler ve sonucu değerlendirir.
        Döndürür: True (başarılı) / False (kalıcı hata) / "RETRY" (yeniden
        denenmeli — çağıran taraf bir sonraki denemeye geçmeli).
        """
        returncode = proc.wait()
        full_output = "\n".join(output_lines)

        if returncode == 0:
            item.status   = "Tamamlandı"
            item.end_time = datetime.now().isoformat()
            self._ui_update(self._update_row, item, "Tamamlandı ✓", "100%", "", "", attempt_label)
            self._ui_update(self.progress_bar.SetValue, 100)
            self._ui_update(self.speaker.say, f"Tamamlandı: {item.title[:45]}", 0)
            history_entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {item.title}"
            if item.playlist_title:
                history_entry += f"  (Liste: {item.playlist_title[:40]})"
            self.history.append(history_entry)
            self._ui_update(self._save, HISTORY_FILE, self.history)
            self._ui_update(self._refresh_history)
            self.downloaded_urls.add(url)
            self._ui_update(self._save, DOWNLOADED_URLS_FILE, sorted(self.downloaded_urls))
            self._log_event(f"Tamamlandı: {item.title}")
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
            return "RETRY"

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
            return "RETRY"

        else:
            item.error  = err_str[:120]
            item.status = "Hata"
            self._ui_update(self._update_row, item, "Hata ✗", item.progress, "", "", attempt_label)
            self._log_event(f"Hata: {url[:60]} — {err_str[:100]}")
            self._ui_update(self.speaker.say, f"Hata: {item.title[:35]}", 0)
            # Kalıcı hata sonrası yarım kalan geçici dosyaları temizle.
            removed = cleanup_temp_files(target_dir)
            if removed:
                self._log_event(f"Geçici dosyalar silindi ({len(removed)} adet): {target_dir}")
            return False

    def _handle_cancelled(self, item: DownloadItem, url: str, target_dir: str, attempt_label: str):
        item.error = "İptal edildi"
        self._ui_update(self._update_row, item, "İptal", item.progress, "", "", attempt_label)
        self._log_event(f"İptal: {url[:60]}")
        # İptal edilen indirmeden geriye kalan .part/.ytdl/parça
        # dosyalarını (webm/m4a/mp4 vb.) diskten temizle.
        removed = cleanup_temp_files(target_dir)
        if removed:
            self._log_event(f"Geçici dosyalar silindi ({len(removed)} adet): {target_dir}")

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

        total = len(expanded)

        # ── Eşzamanlılık durumu ────────────────────────────────────────────
        # "Dönüştürülüyor" (ffmpeg postprocess) aşaması ağdan değil CPU'dan
        # beslenir. Bir video bu aşamaya girdiğinde kuyruk BEKLEMEDEN bir
        # sonraki videonun indirmesine başlar; dönüştürme ayrı bir arka plan
        # thread'inde tamamlanır (bkz. _read_download_phase / DownloadEngine
        # tarafındaki devretme mantığı). Aynı anda en fazla BİR video
        # dönüştürülür — böylece zayıf bilgisayarlarda birden fazla ffmpeg
        # süreci üst üste yığılmaz.
        self._pp_semaphore   = threading.BoundedSemaphore(1)
        self._pending_threads = []
        self._counts_lock    = threading.Lock()
        self._counts = {'completed': 0, 'failed': 0, 'done': 0}

        def on_item_done(url, item, success):
            with self._counts_lock:
                if success:
                    self._counts['completed'] += 1
                else:
                    self._counts['failed'] += 1
                    if not self.stop_event.is_set() and url not in self.failed_urls:
                        self.failed_urls.append(url)
                self._counts['done'] += 1
                done = self._counts['done']
            self._ui_update(self.progress_bar.SetValue, int(done / total * 100))

        # Arka plan thread'lerinin (_spawn_background_finish) erişebilmesi
        # için bir metod referansı olarak saklanır.
        self._on_item_done = on_item_done

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

            result = self._download_single(url, item, fmt)

            if result == "PENDING":
                # Bu videonun dönüştürmesi arka planda sürüyor; sayaçlar ve
                # ilerleme çubuğu, o thread bittiğinde on_item_done ile
                # otomatik güncellenecek — burada BEKLENMİYOR, kuyruk hemen
                # bir sonraki videoya geçiyor.
                pass
            else:
                on_item_done(url, item, bool(result))

            if idx < total - 1 and not self.stop_event.is_set():
                mn = self.settings.get('between_dl_min', BETWEEN_DL_MIN)
                mx = self.settings.get('between_dl_max', BETWEEN_DL_MAX)
                wait = random.uniform(mn, mx)
                self._ui_update(self.speaker.say,
                                f"Sonraki video için {wait:.1f}s bekleniyor.", 0)
                deadline = time.time() + wait
                while time.time() < deadline:
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.3)

        # Arka planda hâlâ dönüştürülmekte olan videolar varsa, özet ve
        # bildirim gösterilmeden önce bitmelerini bekle.
        for t in self._pending_threads:
            t.join()

        completed = self._counts['completed']
        failed    = self._counts['failed']

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
