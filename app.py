"""
MediaGrab v2  –  Professional Video Downloader
Supports single videos and full playlists.
"""
import sys, os, threading, queue, io, urllib.request, re
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image
import yt_dlp

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
_FFMPEG = os.path.join(_HERE, '..', 'ffmpeg-2026-05-18-git-b4d11dffbf-essentials_build', 'bin', 'ffmpeg.exe')
_NODE   = r'C:\Program Files\nodejs\node.exe'
_CACHE  = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'MediaGrab', 'cache')
os.makedirs(_CACHE, exist_ok=True)

# ── Palette ────────────────────────────────────────────────────────────────────
C = dict(
    bg       = '#0a0a14',
    bg2      = '#12121f',
    card     = '#16162a',
    card2    = '#1e1e38',
    border   = '#2a2a50',
    accent   = '#7057f5',
    accent2  = '#5a44d9',
    text     = '#f0f0ff',
    sub      = '#8888aa',
    success  = '#4fd18a',
    error    = '#f05a6a',
    warn     = '#f0c040',
    row_odd  = '#13132a',
    row_even = '#16162e',
)

FONT      = ('Segoe UI', 11)
FONT_SM   = ('Segoe UI', 9)
FONT_LG   = ('Segoe UI', 14, 'bold')
FONT_MONO = ('Consolas', 9)

# ── Data maps ──────────────────────────────────────────────────────────────────
RESOLUTION_LABELS = {
    144:  '144p',
    240:  '240p',
    360:  '360p',
    480:  '480p — SD',
    720:  '720p — HD',
    1080: '1080p — Full HD',
    1440: '1440p — 2K',
    2160: '2160p — 4K Ultra HD',
    4320: '4320p — 8K Ultra HD',
}

KNOWN_LANGS = {
    'en': 'English', 'en-US': 'English (US)', 'en-orig': 'English (Original)',
    'ta': 'Tamil', 'hi': 'Hindi', 'te': 'Telugu', 'ml': 'Malayalam',
    'kn': 'Kannada', 'bn': 'Bengali', 'pa': 'Punjabi', 'mr': 'Marathi',
    'gu': 'Gujarati', 'or': 'Odia', 'si': 'Sinhala',
    'ar': 'Arabic', 'fr': 'French', 'fr-FR': 'French',
    'de': 'German', 'de-DE': 'German',
    'es': 'Spanish', 'es-US': 'Spanish (US)',
    'pt-BR': 'Portuguese (BR)', 'pt-PT': 'Portuguese (PT)',
    'ru': 'Russian', 'uk': 'Ukrainian', 'pl': 'Polish',
    'ja': 'Japanese', 'ko': 'Korean',
    'zh-Hans': 'Chinese (Simplified)', 'zh-Hant': 'Chinese (Traditional)',
    'it': 'Italian', 'nl': 'Dutch', 'nl-NL': 'Dutch',
    'id': 'Indonesian', 'tr': 'Turkish', 'vi': 'Vietnamese', 'th': 'Thai',
}

# ── Format helpers ─────────────────────────────────────────────────────────────
class FormatData:
    def __init__(self):
        self.title      = ''
        self.channel    = ''
        self.duration   = 0
        self.thumbnail  = ''
        self.view_count = 0
        self.resolutions: list[int]       = []
        self.languages:   dict[str, str]  = {}
        self.hls_map:     dict[tuple,str] = {}

def parse_info(info: dict) -> FormatData:
    fd = FormatData()
    fd.title      = info.get('title', '')
    fd.channel    = info.get('channel', '') or info.get('uploader', '')
    fd.duration   = info.get('duration', 0) or 0
    fd.thumbnail  = info.get('thumbnail', '')
    fd.view_count = info.get('view_count', 0) or 0
    res_set, lang_map = set(), {}
    for f in info.get('formats', []):
        h    = f.get('height')
        lang = f.get('language')
        hv   = f.get('vcodec', 'none') not in (None, 'none')
        ha   = f.get('acodec', 'none') not in (None, 'none')
        proto= f.get('protocol', '')
        if hv and ha and 'm3u8' in proto and h and lang:
            res_set.add(h)
            lang_map.setdefault(lang, KNOWN_LANGS.get(lang, lang))
            fd.hls_map.setdefault((h, lang), f['format_id'])
        if hv and not ha and h:
            res_set.add(h)
        if not hv and ha and lang:
            lang_map.setdefault(lang, KNOWN_LANGS.get(lang, lang))
    fd.resolutions = sorted(res_set)
    fd.languages   = lang_map
    return fd

def make_fmt(h: int, lang: str, fd: FormatData) -> str:
    k = (h, lang)
    if k in fd.hls_map:
        return fd.hls_map[k]
    return (f'bestvideo[height<={h}][ext=mp4]/bestvideo[height<={h}]'
            f'+bestaudio[language={lang}]/bestaudio')

# ── Utility ────────────────────────────────────────────────────────────────────
def fmt_dur(s):
    if not s: return ''
    m, s = divmod(int(s), 60); h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

def fmt_views(n):
    if not n: return ''
    if n >= 1_000_000: return f'{n/1_000_000:.1f}M views'
    if n >= 1_000:     return f'{n/1_000:.0f}K views'
    return f'{n} views'

def fmt_speed(bps):
    if not bps: return '—'
    for u in ('B','KB','MB','GB'):
        if bps < 1024: return f'{bps:.1f} {u}/s'
        bps /= 1024
    return f'{bps:.1f} TB/s'

def fmt_eta(s):
    if s is None: return ''
    s = int(s); m, s = divmod(s, 60)
    return f'ETA {m}:{s:02d}'

def is_playlist(url: str) -> bool:
    return bool(re.search(r'[?&]list=', url)) or '/playlist' in url


# ══════════════════════════════════════════════════════════════════════════════
#  Main Application
# ══════════════════════════════════════════════════════════════════════════════
class MediaGrabApp(ctk.CTk):

    SPIN = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']

    def __init__(self):
        super().__init__()
        self.title('MediaGrab')
        self.geometry('940x860')
        self.minsize(780, 660)
        self.configure(fg_color=C['bg'])

        self._fd:        FormatData | None = None
        self._log_q:     queue.Queue  = queue.Queue()
        self._spin_idx:  int  = 0
        self._spinning:  bool = False
        self._thumb_ref        = None
        # playlist state
        self._pl_entries: list[dict] = []   # [{id, title, duration, url, status}]
        self._pl_rows:    list       = []   # CTkFrame rows in the playlist table
        self._dl_stop:    bool       = False

        self._build_ui()
        self._poll_log()

    # ── yt-dlp opts ───────────────────────────────────────────────────────────
    def _ydl_opts(self, skip_hls=False, **kw):
        o = {'quiet': True, 'no_warnings': False, 'cachedir': _CACHE}
        if os.path.isfile(_NODE):
            o['js_runtimes']      = f'node:{_NODE}'
            o['remote_components'] = 'ejs:github'
        if os.path.isfile(_FFMPEG):
            o['ffmpeg_location'] = os.path.dirname(_FFMPEG)
        if skip_hls:
            o['extractor_args'] = {'youtube': {'skip': ['hls']}}
        o.update(kw)
        return o

    def _logger(self):
        app = self
        class L:
            def debug(s,m):
                if not m.startswith('[debug]'): app._log(m)
            def info(s,m):    app._log(m)
            def warning(s,m): app._log(f'⚠  {m}')
            def error(s,m):   app._log(f'✖  {m}')
        return L()

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header bar
        hdr = ctk.CTkFrame(self, fg_color=C['bg2'], corner_radius=0, height=64)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        logo = ctk.CTkFrame(hdr, fg_color='transparent')
        logo.pack(side='left', padx=24, pady=12)
        ctk.CTkLabel(logo, text='Media', font=('Segoe UI', 20, 'bold'),
                     text_color=C['text']).pack(side='left')
        ctk.CTkLabel(logo, text='Grab', font=('Segoe UI', 20, 'bold'),
                     text_color=C['accent']).pack(side='left')
        ctk.CTkLabel(hdr, text='Download videos & playlists · any quality · any language',
                     font=FONT_SM, text_color=C['sub']).pack(side='left', padx=6)

        # Scrollable body
        body = ctk.CTkScrollableFrame(self, fg_color=C['bg'], corner_radius=0)
        body.pack(fill='both', expand=True)
        self._body = body

        # ── URL card ──────────────────────────────────────────────────────────
        uc = self._card(body, (20, 10))
        ctk.CTkLabel(uc, text='Video or Playlist URL',
                     font=('Segoe UI', 10, 'bold'), text_color=C['sub']
                     ).pack(anchor='w', padx=18, pady=(14, 4))
        ur = ctk.CTkFrame(uc, fg_color='transparent')
        ur.pack(fill='x', padx=14, pady=(0, 4))
        self._url_entry = ctk.CTkEntry(
            ur, placeholder_text='Paste YouTube video or playlist URL…',
            font=FONT, fg_color=C['bg2'], border_color=C['border'],
            text_color=C['text'], placeholder_text_color=C['sub'],
            border_width=1, corner_radius=10, height=44)
        self._url_entry.pack(side='left', fill='x', expand=True, padx=(0, 10))
        self._url_entry.bind('<Return>', lambda _: self._fetch())
        self._fetch_btn = ctk.CTkButton(
            ur, text='  Fetch  ', font=('Segoe UI', 11, 'bold'),
            fg_color=C['accent'], hover_color=C['accent2'],
            corner_radius=10, height=44, width=110, command=self._fetch)
        self._fetch_btn.pack(side='left')
        self._spin_lbl = ctk.CTkLabel(uc, text='', font=FONT_SM, text_color=C['sub'])
        self._spin_lbl.pack(anchor='w', padx=18, pady=(2, 10))

        # ── Video info card ────────────────────────────────────────────────────
        self._info_card = self._card(body, (0, 10))
        self._info_card.pack_forget()
        ii = ctk.CTkFrame(self._info_card, fg_color='transparent')
        ii.pack(fill='x', padx=16, pady=14)
        self._thumb_lbl = ctk.CTkLabel(ii, text='', width=180, height=102,
                                        fg_color=C['bg2'], corner_radius=8,
                                        text_color=C['sub'])
        self._thumb_lbl.pack(side='left', padx=(0, 16))
        meta = ctk.CTkFrame(ii, fg_color='transparent')
        meta.pack(side='left', fill='x', expand=True)
        self._title_lbl   = ctk.CTkLabel(meta, text='', font=FONT_LG,
                                          text_color=C['text'],
                                          wraplength=580, justify='left', anchor='w')
        self._title_lbl.pack(anchor='w', pady=(4, 6))
        self._channel_lbl = ctk.CTkLabel(meta, text='', font=FONT_SM,
                                          text_color=C['sub'])
        self._channel_lbl.pack(anchor='w')
        br = ctk.CTkFrame(meta, fg_color='transparent')
        br.pack(anchor='w', pady=(8, 0))
        self._dur_badge   = self._badge(br, '⏱ —')
        self._views_badge = self._badge(br, '👁 —')
        self._lang_badge  = self._badge(br, '🔊 —')

        # ── Playlist table card ────────────────────────────────────────────────
        self._pl_card = self._card(body, (0, 10))
        self._pl_card.pack_forget()
        pl_hdr = ctk.CTkFrame(self._pl_card, fg_color='transparent')
        pl_hdr.pack(fill='x', padx=16, pady=(12, 6))
        self._pl_title_lbl = ctk.CTkLabel(pl_hdr, text='', font=FONT_LG,
                                           text_color=C['text'])
        self._pl_title_lbl.pack(side='left')
        self._pl_count_lbl = ctk.CTkLabel(pl_hdr, text='', font=FONT_SM,
                                           text_color=C['sub'])
        self._pl_count_lbl.pack(side='left', padx=12)
        # Column headers
        col_hdr = ctk.CTkFrame(self._pl_card, fg_color=C['bg2'], corner_radius=0)
        col_hdr.pack(fill='x', padx=16)
        for txt, w in [('#', 40), ('Title', 0), ('Duration', 80), ('Status', 100)]:
            ctk.CTkLabel(col_hdr, text=txt, font=('Segoe UI', 9, 'bold'),
                         text_color=C['sub'], width=w if w else 0,
                         anchor='w').pack(side='left', padx=(8 if txt == '#' else 4, 4),
                                          pady=6, expand=(txt == 'Title'))
        # Scrollable rows container
        self._pl_rows_frame = ctk.CTkScrollableFrame(
            self._pl_card, fg_color=C['card'], corner_radius=0, height=260)
        self._pl_rows_frame.pack(fill='x', padx=16, pady=(0, 10))

        # ── Options card ──────────────────────────────────────────────────────
        self._opt_card = self._card(body, (0, 10))
        self._opt_card.pack_forget()
        oi = ctk.CTkFrame(self._opt_card, fg_color='transparent')
        oi.pack(fill='x', padx=16, pady=14)
        qc = ctk.CTkFrame(oi, fg_color='transparent')
        qc.pack(side='left', fill='x', expand=True, padx=(0, 12))
        ctk.CTkLabel(qc, text='Quality', font=('Segoe UI', 10, 'bold'),
                     text_color=C['sub']).pack(anchor='w', pady=(0, 6))
        self._quality_var = tk.StringVar(value='—')
        self._quality_cb  = ctk.CTkComboBox(
            qc, variable=self._quality_var, font=FONT,
            fg_color=C['bg2'], border_color=C['border'],
            button_color=C['accent'], button_hover_color=C['accent2'],
            dropdown_fg_color=C['card2'], dropdown_text_color=C['text'],
            dropdown_hover_color=C['accent'],
            text_color=C['text'], corner_radius=10, height=42,
            state='disabled', command=self._on_opt_change)
        self._quality_cb.pack(fill='x')

        lc = ctk.CTkFrame(oi, fg_color='transparent')
        lc.pack(side='left', fill='x', expand=True)
        ctk.CTkLabel(lc, text='Audio Language', font=('Segoe UI', 10, 'bold'),
                     text_color=C['sub']).pack(anchor='w', pady=(0, 6))
        self._lang_var = tk.StringVar(value='—')
        self._lang_cb  = ctk.CTkComboBox(
            lc, variable=self._lang_var, font=FONT,
            fg_color=C['bg2'], border_color=C['border'],
            button_color=C['accent'], button_hover_color=C['accent2'],
            dropdown_fg_color=C['card2'], dropdown_text_color=C['text'],
            dropdown_hover_color=C['accent'],
            text_color=C['text'], corner_radius=10, height=42,
            state='disabled', command=self._on_opt_change)
        self._lang_cb.pack(fill='x')
        self._fmt_hint = ctk.CTkLabel(self._opt_card, text='', font=FONT_SM,
                                       text_color=C['sub'])
        self._fmt_hint.pack(anchor='w', padx=18, pady=(0, 10))

        # ── Save-to card ──────────────────────────────────────────────────────
        self._save_card = self._card(body, (0, 10))
        self._save_card.pack_forget()
        si = ctk.CTkFrame(self._save_card, fg_color='transparent')
        si.pack(fill='x', padx=16, pady=14)
        ctk.CTkLabel(si, text='Save to', font=('Segoe UI', 10, 'bold'),
                     text_color=C['sub']).pack(anchor='w', pady=(0, 6))
        sr = ctk.CTkFrame(si, fg_color='transparent')
        sr.pack(fill='x')
        self._out_entry = ctk.CTkEntry(
            sr, font=FONT, fg_color=C['bg2'], border_color=C['border'],
            text_color=C['text'], border_width=1, corner_radius=10, height=42)
        self._out_entry.insert(0, os.path.expanduser('~/Downloads'))
        self._out_entry.pack(side='left', fill='x', expand=True, padx=(0, 10))
        ctk.CTkButton(sr, text='Browse', font=FONT, width=90, height=42,
                      fg_color=C['card2'], hover_color=C['border'],
                      text_color=C['text'], corner_radius=10,
                      command=self._browse).pack(side='left')

        # ── Download card ─────────────────────────────────────────────────────
        self._dl_card = self._card(body, (0, 10))
        self._dl_card.pack_forget()
        di = ctk.CTkFrame(self._dl_card, fg_color='transparent')
        di.pack(fill='x', padx=16, pady=16)

        btn_row = ctk.CTkFrame(di, fg_color='transparent')
        btn_row.pack(fill='x', pady=(0, 14))
        self._dl_btn = ctk.CTkButton(
            btn_row, text='▶  Download Now',
            font=('Segoe UI', 13, 'bold'),
            fg_color=C['accent'], hover_color=C['accent2'],
            corner_radius=12, height=52, command=self._download)
        self._dl_btn.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self._stop_btn = ctk.CTkButton(
            btn_row, text='■ Stop', font=FONT,
            fg_color=C['card2'], hover_color=C['error'],
            text_color=C['sub'], corner_radius=12,
            height=52, width=90, command=self._stop_dl)
        self._stop_btn.pack(side='left')

        # Overall progress
        op = ctk.CTkFrame(di, fg_color='transparent')
        op.pack(fill='x', pady=(0, 6))
        self._pct_lbl  = ctk.CTkLabel(op, text='', font=('Segoe UI', 10, 'bold'),
                                        text_color=C['text'], width=50)
        self._pct_lbl.pack(side='left', padx=(0, 10))
        self._prog_bar = ctk.CTkProgressBar(op, fg_color=C['bg2'],
                                             progress_color=C['accent'],
                                             corner_radius=6, height=10)
        self._prog_bar.set(0)
        self._prog_bar.pack(side='left', fill='x', expand=True, padx=(0, 10))
        self._eta_lbl  = ctk.CTkLabel(op, text='', font=FONT_SM,
                                        text_color=C['sub'], width=150, anchor='e')
        self._eta_lbl.pack(side='left')

        # Playlist overall progress
        self._pl_prog_lbl = ctk.CTkLabel(di, text='', font=FONT_SM,
                                          text_color=C['sub'])
        self._pl_prog_lbl.pack(anchor='w')

        # ── Log card ──────────────────────────────────────────────────────────
        lc2 = self._card(body, (0, 20))
        lh  = ctk.CTkFrame(lc2, fg_color='transparent')
        lh.pack(fill='x', padx=16, pady=(10, 4))
        ctk.CTkLabel(lh, text='Log', font=('Segoe UI', 10, 'bold'),
                     text_color=C['sub']).pack(side='left')
        ctk.CTkButton(lh, text='Clear', font=FONT_SM, width=60, height=26,
                      fg_color=C['bg2'], hover_color=C['border'],
                      text_color=C['sub'], corner_radius=6,
                      command=self._clear_log).pack(side='right')
        self._log_box = ctk.CTkTextbox(lc2, height=130, font=FONT_MONO,
                                        fg_color=C['bg2'], text_color=C['text'],
                                        border_width=0, corner_radius=8,
                                        wrap='word', state='disabled')
        self._log_box.pack(fill='x', padx=14, pady=(0, 14))

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _card(self, parent, pady=(0,10)):
        c = ctk.CTkFrame(parent, fg_color=C['card'], corner_radius=14,
                          border_width=1, border_color=C['border'])
        c.pack(fill='x', padx=20, pady=pady)
        return c

    def _badge(self, parent, text):
        b = ctk.CTkLabel(parent, text=text, font=FONT_SM,
                          fg_color=C['bg2'], text_color=C['sub'],
                          corner_radius=8, padx=8, pady=3)
        b.pack(side='left', padx=(0, 8))
        return b

    def _show_cards(self, *cards):
        for c in cards:
            c.pack(fill='x', padx=20, pady=(0, 10))

    def _hide_cards(self, *cards):
        for c in cards:
            c.pack_forget()

    def _browse(self):
        d = filedialog.askdirectory(title='Select output folder')
        if d:
            self._out_entry.delete(0, 'end')
            self._out_entry.insert(0, d)

    def _sel_height(self):
        v = self._quality_var.get()
        for h, l in RESOLUTION_LABELS.items():
            if l == v: return h
        try: return int(v.split('p')[0])
        except: return None

    def _sel_lang(self):
        v = self._lang_var.get()
        return v.rsplit('(', 1)[-1].rstrip(')').strip() if '(' in v else None

    # ── Spinner ───────────────────────────────────────────────────────────────
    def _spin_start(self, msg='Working…'):
        self._spinning = True; self._spin_msg = msg; self._spin_tick()

    def _spin_tick(self):
        if not self._spinning: return
        f = self.SPIN[self._spin_idx % len(self.SPIN)]
        self._spin_lbl.configure(text=f'{f}  {self._spin_msg}')
        self._spin_idx += 1
        self.after(80, self._spin_tick)

    def _spin_stop(self, msg=''):
        self._spinning = False
        self._spin_lbl.configure(text=msg)

    # ── Fetch (detects video vs playlist) ────────────────────────────────────
    def _fetch(self):
        url = self._url_entry.get().strip()
        if not url: return
        self._fetch_btn.configure(state='disabled', text='Fetching…')
        self._hide_cards(self._info_card, self._pl_card,
                          self._opt_card, self._save_card, self._dl_card)
        self._spin_start('Contacting server…')
        self._log(f'→ {url}')

        if is_playlist(url):
            threading.Thread(target=self._fetch_playlist, args=(url,),
                             daemon=True).start()
        else:
            threading.Thread(target=self._fetch_video_p1, args=(url,),
                             daemon=True).start()

    # ── Single video fetch (2-phase) ─────────────────────────────────────────
    def _fetch_video_p1(self, url):
        try:
            opts = self._ydl_opts(skip_hls=True, logger=self._logger())
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            fd = parse_info(info)
            self.after(0, lambda: self._show_video_p1(fd, url))
        except Exception as e:
            self.after(0, lambda: self._fetch_err(e))

    def _show_video_p1(self, fd: FormatData, url: str):
        self._fd = fd
        self._fill_video_card(fd)
        self._fill_quality(fd)
        self._fill_lang(fd)
        self._show_cards(self._info_card, self._opt_card,
                          self._save_card, self._dl_card)
        self._spin_stop('Loading all audio languages…')
        self._log(f'✔ {fd.title}  ({fmt_dur(fd.duration)})')

        def _p2():
            try:
                opts = self._ydl_opts(logger=self._logger())
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                fd2 = parse_info(info)
                self.after(0, lambda: self._show_video_p2(fd2))
            except Exception as e:
                self.after(0, lambda: self._spin_stop(f'⚠  {e}'))
        threading.Thread(target=_p2, daemon=True).start()

    def _show_video_p2(self, fd: FormatData):
        self._fd = fd
        self._fill_lang(fd)
        n = len(fd.languages)
        self._lang_badge.configure(text=f'🔊 {n} audio tracks')
        self._log(f'✔ {n} audio languages found')
        self._spin_stop('Ready')
        self._fetch_btn.configure(state='normal', text='  Fetch  ')

    # ── Playlist fetch ────────────────────────────────────────────────────────
    def _fetch_playlist(self, url):
        try:
            opts = self._ydl_opts(
                extract_flat=True, quiet=True,
                extractor_args={'youtube': {'skip': ['webpage', 'configs', 'hls', 'dash']}})
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if info.get('_type') != 'playlist':
                # Treated as single video
                fd = parse_info(info)
                self.after(0, lambda: self._show_video_p1(fd, url))
                return

            entries = [
                {'idx': i + 1,
                 'id':  e.get('id', ''),
                 'title': e.get('title', f'Video {i+1}'),
                 'duration': e.get('duration', 0),
                 'url': e.get('url') or e.get('webpage_url') or
                        f"https://www.youtube.com/watch?v={e.get('id','')}",
                 'status': 'Pending'}
                for i, e in enumerate(info.get('entries', []) or [])
            ]
            pl_title  = info.get('title', 'Playlist')
            pl_channel = info.get('channel', '') or info.get('uploader', '')

            self.after(0, lambda: self._show_playlist(pl_title, pl_channel, entries, url))
        except Exception as e:
            self.after(0, lambda: self._fetch_err(e))

    def _show_playlist(self, pl_title, channel, entries, url):
        self._pl_entries = entries
        self._spin_stop(f'✔ {len(entries)} videos in playlist')
        self._fetch_btn.configure(state='normal', text='  Fetch  ')

        # Also fetch format options from the first video so dropdowns are populated
        self._pl_title_lbl.configure(text=pl_title)
        self._pl_count_lbl.configure(
            text=f'{len(entries)} videos  •  {channel}')

        # Clear old rows
        for w in self._pl_rows_frame.winfo_children():
            w.destroy()
        self._pl_rows = []

        for e in entries:
            bg = C['row_odd'] if e['idx'] % 2 else C['row_even']
            row = ctk.CTkFrame(self._pl_rows_frame, fg_color=bg, corner_radius=0)
            row.pack(fill='x')
            ctk.CTkLabel(row, text=str(e['idx']), width=40, font=FONT_SM,
                         text_color=C['sub'], anchor='w').pack(side='left', padx=8)
            ctk.CTkLabel(row, text=e['title'], font=FONT_SM,
                         text_color=C['text'], anchor='w').pack(
                             side='left', fill='x', expand=True, padx=4)
            ctk.CTkLabel(row, text=fmt_dur(e['duration']), width=80,
                         font=FONT_SM, text_color=C['sub']).pack(side='left', padx=4)
            status_lbl = ctk.CTkLabel(row, text=e['status'], width=100,
                                       font=FONT_SM, text_color=C['sub'])
            status_lbl.pack(side='left', padx=(4, 8))
            e['_lbl'] = status_lbl  # reference for live updates

        self._log(f'✔ Playlist: {pl_title}  ({len(entries)} videos)')

        # Fetch format options from first video
        if entries:
            first_url = entries[0]['url']
            threading.Thread(
                target=self._fetch_pl_formats, args=(first_url,),
                daemon=True).start()

        self._show_cards(self._pl_card, self._opt_card,
                          self._save_card, self._dl_card)

    def _fetch_pl_formats(self, url):
        try:
            self.after(0, lambda: self._spin_start('Fetching format options…'))
            opts = self._ydl_opts(logger=self._logger())
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            fd = parse_info(info)
            self.after(0, lambda: self._show_pl_formats(fd))
        except Exception as e:
            self.after(0, lambda: self._spin_stop(f'⚠  {e}'))

    def _show_pl_formats(self, fd: FormatData):
        self._fd = fd
        self._fill_quality(fd)
        self._fill_lang(fd)
        n = len(fd.languages)
        self._log(f'✔ Formats loaded — {n} audio languages available')
        self._spin_stop('Ready — select quality & language, then Download All')

    # ── Fill dropdowns ────────────────────────────────────────────────────────
    def _fill_video_card(self, fd: FormatData):
        self._title_lbl.configure(text=fd.title)
        self._channel_lbl.configure(text=fd.channel)
        self._dur_badge.configure(text=f'⏱ {fmt_dur(fd.duration)}')
        self._views_badge.configure(text=fmt_views(fd.view_count))
        self._lang_badge.configure(text='🔊 loading…')
        if fd.thumbnail:
            threading.Thread(target=self._load_thumb, args=(fd.thumbnail,),
                             daemon=True).start()

    def _load_thumb(self, url):
        try:
            req  = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = urllib.request.urlopen(req, timeout=10).read()
            img  = Image.open(io.BytesIO(data)).resize((180, 101), Image.LANCZOS)
            ci   = ctk.CTkImage(light_image=img, dark_image=img, size=(180, 101))
            self.after(0, lambda: self._thumb_lbl.configure(image=ci, text=''))
            self._thumb_ref = ci
        except Exception:
            pass

    def _fill_quality(self, fd: FormatData):
        labels = [RESOLUTION_LABELS.get(r, f'{r}p') for r in reversed(fd.resolutions)]
        self._quality_cb.configure(values=labels, state='readonly')
        if labels: self._quality_var.set(labels[0])

    def _fill_lang(self, fd: FormatData):
        labels = [f'{n}  ({c})' for c, n in
                  sorted(fd.languages.items(), key=lambda x: x[1])]
        saved  = self._lang_var.get()
        self._lang_cb.configure(values=labels, state='readonly')
        if saved in labels:
            self._lang_var.set(saved)
        else:
            default = next((l for l in labels if 'English' in l),
                           labels[0] if labels else '')
            self._lang_var.set(default)
        self._on_opt_change()

    def _on_opt_change(self, *_):
        if not self._fd: return
        h = self._sel_height(); l = self._sel_lang()
        if h and l:
            fmt = make_fmt(h, l, self._fd)
            self._fmt_hint.configure(
                text=f'Format: {fmt}  ·  {RESOLUTION_LABELS.get(h, f"{h}p")}')

    def _fetch_err(self, e):
        self._fetch_btn.configure(state='normal', text='  Fetch  ')
        self._spin_stop(f'✖  {e}')
        self._log(f'✖  {e}')
        messagebox.showerror('MediaGrab', f'Could not fetch:\n{e}')

    # ── Download (single or playlist) ─────────────────────────────────────────
    def _download(self):
        if not self._fd: return
        outdir = self._out_entry.get().strip()
        h = self._sel_height(); l = self._sel_lang()
        if not os.path.isdir(outdir):
            messagebox.showerror('MediaGrab', 'Output folder does not exist.')
            return
        if not h or not l:
            messagebox.showerror('MediaGrab', 'Select quality and audio language.')
            return

        self._dl_stop = False
        self._dl_btn.configure(state='disabled', text='Downloading…')

        if self._pl_entries:
            # Playlist mode
            threading.Thread(target=self._dl_playlist,
                             args=(outdir, h, l), daemon=True).start()
        else:
            # Single video
            url = self._url_entry.get().strip()
            fmt = make_fmt(h, l, self._fd)
            threading.Thread(target=self._dl_single,
                             args=(url, fmt, outdir, None), daemon=True).start()

    def _stop_dl(self):
        self._dl_stop = True
        self._log('⏹  Stop requested…')
        self._spin_start('Stopping after current video…')

    # ── Single download ───────────────────────────────────────────────────────
    def _dl_single(self, url, fmt, outdir, pl_entry):
        def _hook(d):
            s = d.get('status')
            if s == 'downloading':
                dl    = d.get('downloaded_bytes', 0) or 0
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                fi, fc = (d.get('fragment_index', 0) or 0,
                           d.get('fragment_count',  0) or 0)
                pct = (dl / total * 100) if total else ((fi / fc * 100) if fc else 0)
                pct = min(pct, 99)
                self.after(0, lambda: (
                    self._prog_bar.set(pct / 100),
                    self._pct_lbl.configure(text=f'{pct:.1f}%'),
                    self._eta_lbl.configure(
                        text=f'{fmt_speed(d.get("speed"))}  {fmt_eta(d.get("eta"))}')
                ))
                if pl_entry:
                    self.after(0, lambda: pl_entry['_lbl'].configure(
                        text=f'{pct:.0f}%', text_color=C['accent']))
            elif s == 'finished':
                self.after(0, lambda: self._pct_lbl.configure(text='99%'))

        try:
            outtmpl = os.path.join(outdir, '%(title)s [%(language)s].%(ext)s')
            opts = self._ydl_opts(
                format=fmt, outtmpl=outtmpl,
                merge_output_format='mp4',
                progress_hooks=[_hook],
                logger=self._logger(), quiet=False)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return True
        except Exception as e:
            self._log(f'✖  {e}')
            if pl_entry:
                self.after(0, lambda: pl_entry['_lbl'].configure(
                    text='Error', text_color=C['error']))
            return False

    # ── Playlist download ─────────────────────────────────────────────────────
    def _dl_playlist(self, outdir, h, l):
        total   = len(self._pl_entries)
        done    = 0
        failed  = 0

        for e in self._pl_entries:
            if self._dl_stop:
                self.after(0, lambda: e['_lbl'].configure(
                    text='Skipped', text_color=C['warn']))
                continue

            self.after(0, lambda en=e: en['_lbl'].configure(
                text='⬇ Downloading', text_color=C['accent']))
            self._log(f'[{e["idx"]}/{total}] {e["title"]}')

            # Each playlist video may have different formats; fetch its own format
            try:
                opts = self._ydl_opts(logger=self._logger())
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(e['url'], download=False)
                vfd  = parse_info(info)
                fmt  = make_fmt(h, l, vfd)
            except Exception as ex:
                self._log(f'  ⚠ Could not fetch format: {ex}  — trying best default')
                fmt = f'bestvideo[height<={h}]+bestaudio/best[height<={h}]'

            ok = self._dl_single(e['url'], fmt, outdir, e)

            if ok:
                done += 1
                self.after(0, lambda en=e: en['_lbl'].configure(
                    text='✔ Done', text_color=C['success']))
            else:
                failed += 1
                self.after(0, lambda en=e: en['_lbl'].configure(
                    text='✖ Error', text_color=C['error']))

            self.after(0, lambda d=done, t=total: (
                self._pl_prog_lbl.configure(
                    text=f'Playlist: {d}/{t} downloaded'),
                self._prog_bar.set(d / t),
                self._pct_lbl.configure(text=f'{d}/{t}')
            ))

        self.after(0, lambda: self._dl_playlist_done(done, failed, total))

    def _dl_playlist_done(self, done, failed, total):
        self._dl_btn.configure(state='normal', text='▶  Download Now')
        self._prog_bar.set(1.0)
        self._pct_lbl.configure(text='Done')
        self._spin_stop()
        msg = f'Playlist complete!\n{done}/{total} downloaded'
        if failed:
            msg += f'\n{failed} failed — check log.'
        self._log(f'✔ Playlist done: {done}/{total}  failed:{failed}')
        messagebox.showinfo('MediaGrab', msg)

    # ── Log ───────────────────────────────────────────────────────────────────
    def _log(self, msg):
        self._log_q.put(msg)

    def _poll_log(self):
        while not self._log_q.empty():
            m = self._log_q.get_nowait()
            self._log_box.configure(state='normal')
            self._log_box.insert('end', m + '\n')
            self._log_box.see('end')
            self._log_box.configure(state='disabled')
        self.after(100, self._poll_log)

    def _clear_log(self):
        self._log_box.configure(state='normal')
        self._log_box.delete('1.0', 'end')
        self._log_box.configure(state='disabled')


# ── Entry ──────────────────────────────────────────────────────────────────────
def main():
    app = MediaGrabApp()
    app.mainloop()

if __name__ == '__main__':
    main()
