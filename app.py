"""
MediaGrab — Video Downloader
"""

import sys
import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Resolve companion tool paths (works both in dev and frozen .exe)
_HERE = os.path.dirname(os.path.abspath(__file__))
_FFMPEG = os.path.join(_HERE, '..', 'ffmpeg-2026-05-18-git-b4d11dffbf-essentials_build', 'bin', 'ffmpeg.exe')
_NODE = r'C:\Program Files\nodejs\node.exe'

import yt_dlp

# ── Constants ─────────────────────────────────────────────────────────────────

RESOLUTION_LABELS = {
    144:  '144p',
    240:  '240p',
    360:  '360p',
    480:  '480p (SD)',
    720:  '720p (HD)',
    1080: '1080p (Full HD)',
    1440: '1440p (2K)',
    2160: '2160p (4K)',
    4320: '4320p (8K)',
}

KNOWN_LANGS = {
    'en': 'English', 'en-US': 'English (US)', 'en-orig': 'English (Original)',
    'ta': 'Tamil', 'hi': 'Hindi', 'te': 'Telugu', 'ml': 'Malayalam',
    'kn': 'Kannada', 'bn': 'Bengali / Bangla', 'pa': 'Punjabi',
    'mr': 'Marathi', 'gu': 'Gujarati', 'or': 'Odia', 'si': 'Sinhala',
    'ar': 'Arabic', 'fr': 'French', 'fr-FR': 'French',
    'de': 'German', 'de-DE': 'German',
    'es': 'Spanish', 'es-US': 'Spanish (US)',
    'pt': 'Portuguese', 'pt-BR': 'Portuguese (BR)', 'pt-PT': 'Portuguese (PT)',
    'ru': 'Russian', 'uk': 'Ukrainian',
    'ja': 'Japanese', 'ko': 'Korean',
    'zh': 'Chinese', 'zh-Hans': 'Chinese (Simplified)', 'zh-Hant': 'Chinese (Traditional)',
    'it': 'Italian', 'nl': 'Dutch', 'nl-NL': 'Dutch',
    'pl': 'Polish', 'tr': 'Turkish',
    'id': 'Indonesian', 'ms': 'Malay',
    'vi': 'Vietnamese', 'th': 'Thai',
}

# Dark colour palette
BG        = '#12121f'
BG2       = '#1c1c30'
CARD      = '#1f2040'
ACCENT    = '#7c6af7'
ACCENT2   = '#5b4fe0'
TEXT      = '#e8e8f0'
SUBTEXT   = '#9090b0'
SUCCESS   = '#4caf82'
ERROR     = '#e05c6a'
FONT      = ('Segoe UI', 10)
FONT_BIG  = ('Segoe UI', 13, 'bold')
FONT_MONO = ('Consolas', 9)


# ── Format parsing ────────────────────────────────────────────────────────────

class FormatData:
    def __init__(self):
        self.title    = 'Unknown'
        self.duration = 0
        self.thumb    = None
        # available resolution heights (sorted ascending)
        self.resolutions: list[int] = []
        # available audio languages  {code: display_name}
        self.languages: dict[str, str] = {}
        # (height, lang_code) → yt-dlp format_id  (for HLS dubbed bundles)
        self.hls_map: dict[tuple, str] = {}
        # raw info dict for potential re-use
        self.raw = None


def parse_info(info: dict) -> FormatData:
    fd = FormatData()
    fd.title    = info.get('title', 'Unknown')
    fd.duration = info.get('duration', 0)
    fd.raw      = info

    res_set  = set()
    lang_set = {}

    for f in info.get('formats', []):
        height   = f.get('height')
        lang     = f.get('language')
        vcodec   = f.get('vcodec', 'none')
        acodec   = f.get('acodec', 'none')
        protocol = f.get('protocol', '')

        has_video = vcodec and vcodec != 'none'
        has_audio = acodec and acodec != 'none'

        # HLS muxed stream (video + dubbed audio)
        if has_video and has_audio and 'm3u8' in protocol and height and lang:
            res_set.add(height)
            if lang not in lang_set:
                lang_set[lang] = KNOWN_LANGS.get(lang, lang)
            key = (height, lang)
            # keep highest-bitrate format for this combo
            if key not in fd.hls_map:
                fd.hls_map[key] = f['format_id']

        # DASH video-only → track resolutions
        if has_video and not has_audio and height:
            res_set.add(height)

        # DASH audio-only → track languages
        if not has_video and has_audio and lang:
            if lang not in lang_set:
                lang_set[lang] = KNOWN_LANGS.get(lang, lang)

    fd.resolutions = sorted(res_set)
    fd.languages   = lang_set
    return fd


def make_format_string(height: int, lang_code: str, fd: FormatData) -> str:
    key = (height, lang_code)
    if key in fd.hls_map:
        return fd.hls_map[key]  # HLS bundle (dubbed)

    # DASH merge: best video at resolution + best audio for language
    video = f'bestvideo[height<={height}][ext=mp4]/bestvideo[height<={height}]'
    audio = f'bestaudio[language={lang_code}]/bestaudio'
    return f'{video}+{audio}'


# ── Progress hook ─────────────────────────────────────────────────────────────

def _fmt_speed(bps):
    if not bps:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if bps < 1024:
            return f'{bps:.1f} {unit}/s'
        bps /= 1024
    return f'{bps:.1f} TB/s'


def _fmt_eta(secs):
    if secs is None:
        return '?'
    secs = int(secs)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def make_progress_hook(cb):
    def hook(d):
        status = d.get('status')
        if status == 'downloading':
            downloaded = d.get('downloaded_bytes', 0) or 0
            total      = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            fi         = d.get('fragment_index', 0) or 0
            fc         = d.get('fragment_count', 0) or 0
            speed      = d.get('speed') or 0
            eta        = d.get('eta')

            if total:
                pct = downloaded / total * 100
            elif fc:
                pct = fi / fc * 100
            else:
                pct = 0

            frag_str = f'  frag {fi}/{fc}' if fc else ''
            cb(min(pct, 99.9), f'{_fmt_speed(speed)}  ETA {_fmt_eta(eta)}{frag_str}')

        elif status == 'finished':
            cb(99.9, 'Merging / processing…')
        elif status == 'error':
            cb(-1, 'Error during download')
    return hook


# ── Main Application ──────────────────────────────────────────────────────────

class MediaGrabApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('MediaGrab')
        self.root.geometry('880x700')
        self.root.minsize(720, 580)
        self.root.configure(bg=BG)

        self._fd: FormatData | None = None
        self._log_q: queue.Queue = queue.Queue()
        self._dl_thread: threading.Thread | None = None

        self._apply_style()
        self._build_ui()
        self._poll_log()

    # ── Style ─────────────────────────────────────────────────────────────────

    def _apply_style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')

        style.configure('.',
            background=BG, foreground=TEXT,
            fieldbackground=BG2, troughcolor=BG2,
            font=FONT)

        style.configure('TFrame',  background=BG)
        style.configure('Card.TFrame', background=CARD, relief='flat')

        style.configure('TLabel',  background=BG,   foreground=TEXT, font=FONT)
        style.configure('Sub.TLabel', background=CARD, foreground=SUBTEXT, font=('Segoe UI', 9))
        style.configure('Title.TLabel', background=CARD, foreground=TEXT, font=FONT_BIG)

        style.configure('TEntry',
            fieldbackground=BG2, foreground=TEXT,
            insertcolor=TEXT, bordercolor=ACCENT,
            relief='flat', padding=6)

        style.configure('TCombobox',
            fieldbackground=BG2, foreground=TEXT,
            selectbackground=ACCENT, selectforeground=TEXT,
            arrowcolor=ACCENT, relief='flat', padding=6)
        style.map('TCombobox',
            fieldbackground=[('readonly', BG2)],
            selectbackground=[('readonly', ACCENT)])

        style.configure('Accent.TButton',
            background=ACCENT, foreground='white',
            relief='flat', padding=(14, 8),
            font=('Segoe UI', 10, 'bold'))
        style.map('Accent.TButton',
            background=[('active', ACCENT2), ('disabled', '#444460')],
            foreground=[('disabled', '#888')])

        style.configure('Small.TButton',
            background=BG2, foreground=TEXT,
            relief='flat', padding=(10, 6))
        style.map('Small.TButton',
            background=[('active', ACCENT)])

        style.configure('TProgressbar',
            troughcolor=BG2, background=ACCENT,
            thickness=8, relief='flat')

        # Option menu colours
        self.root.option_add('*TCombobox*Listbox.background', BG2)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        self.root.option_add('*TCombobox*Listbox.font', FONT)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill='both', expand=True)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(outer, bg=BG)
        hdr.pack(fill='x', pady=(0, 18))
        tk.Label(hdr, text='Media', font=('Segoe UI', 22, 'bold'),
                 bg=BG, fg=TEXT).pack(side='left')
        tk.Label(hdr, text='Grab', font=('Segoe UI', 22, 'bold'),
                 bg=BG, fg=ACCENT).pack(side='left')
        tk.Label(hdr, text='  — Download any video in any quality & language',
                 font=('Segoe UI', 10), bg=BG, fg=SUBTEXT).pack(side='left', pady=4)

        # ── URL card ──────────────────────────────────────────────────────────
        url_card = ttk.Frame(outer, style='Card.TFrame', padding=14)
        url_card.pack(fill='x', pady=(0, 12))

        tk.Label(url_card, text='Video URL', font=('Segoe UI', 9, 'bold'),
                 bg=CARD, fg=SUBTEXT).pack(anchor='w')

        url_row = tk.Frame(url_card, bg=CARD)
        url_row.pack(fill='x', pady=(6, 0))

        self._url_var = tk.StringVar()
        url_entry = tk.Entry(url_row, textvariable=self._url_var,
                             bg=BG2, fg=TEXT, insertbackground=TEXT,
                             relief='flat', font=FONT,
                             highlightthickness=1,
                             highlightbackground=ACCENT,
                             highlightcolor=ACCENT)
        url_entry.pack(side='left', fill='x', expand=True, ipady=8, padx=(0, 10))
        url_entry.bind('<Return>', lambda _: self._fetch())

        self._fetch_btn = ttk.Button(url_row, text='Fetch Formats',
                                     style='Accent.TButton', command=self._fetch)
        self._fetch_btn.pack(side='left')

        # video title (shown after fetch)
        self._title_var = tk.StringVar(value='Paste a YouTube (or other) URL above and click Fetch Formats')
        tk.Label(url_card, textvariable=self._title_var,
                 bg=CARD, fg=SUBTEXT, font=('Segoe UI', 9),
                 wraplength=780, justify='left').pack(anchor='w', pady=(8, 0))

        # ── Options card ──────────────────────────────────────────────────────
        opt_card = ttk.Frame(outer, style='Card.TFrame', padding=14)
        opt_card.pack(fill='x', pady=(0, 12))

        cols = tk.Frame(opt_card, bg=CARD)
        cols.pack(fill='x')

        def _col(parent, label, row=0):
            f = tk.Frame(parent, bg=CARD)
            f.pack(side='left', fill='x', expand=True, padx=(0, 16))
            tk.Label(f, text=label, font=('Segoe UI', 9, 'bold'),
                     bg=CARD, fg=SUBTEXT).pack(anchor='w')
            return f

        # Quality
        qf = _col(cols, 'Quality / Resolution')
        self._quality_var = tk.StringVar(value='— fetch first —')
        self._quality_cb = ttk.Combobox(qf, textvariable=self._quality_var,
                                         state='disabled', font=FONT)
        self._quality_cb.pack(fill='x', pady=(4, 0))

        # Audio language
        lf = _col(cols, 'Audio Language')
        self._lang_var = tk.StringVar(value='— fetch first —')
        self._lang_cb = ttk.Combobox(lf, textvariable=self._lang_var,
                                      state='disabled', font=FONT)
        self._lang_cb.pack(fill='x', pady=(4, 0))

        # Format hint
        self._fmt_hint = tk.StringVar(value='')
        tk.Label(opt_card, textvariable=self._fmt_hint,
                 bg=CARD, fg=SUBTEXT, font=('Segoe UI', 8)).pack(anchor='w', pady=(10, 0))

        # Update hint when combos change
        self._quality_var.trace_add('write', self._update_fmt_hint)
        self._lang_var.trace_add('write', self._update_fmt_hint)

        # ── Output card ───────────────────────────────────────────────────────
        out_card = ttk.Frame(outer, style='Card.TFrame', padding=14)
        out_card.pack(fill='x', pady=(0, 12))

        tk.Label(out_card, text='Save to', font=('Segoe UI', 9, 'bold'),
                 bg=CARD, fg=SUBTEXT).pack(anchor='w')

        out_row = tk.Frame(out_card, bg=CARD)
        out_row.pack(fill='x', pady=(6, 0))

        self._out_var = tk.StringVar(value=os.path.expanduser('~/Downloads'))
        out_entry = tk.Entry(out_row, textvariable=self._out_var,
                             bg=BG2, fg=TEXT, insertbackground=TEXT,
                             relief='flat', font=FONT,
                             highlightthickness=1,
                             highlightbackground=ACCENT,
                             highlightcolor=ACCENT)
        out_entry.pack(side='left', fill='x', expand=True, ipady=8, padx=(0, 10))

        ttk.Button(out_row, text='Browse…', style='Small.TButton',
                   command=self._browse).pack(side='left')

        # ── Progress ──────────────────────────────────────────────────────────
        prog_card = ttk.Frame(outer, style='Card.TFrame', padding=14)
        prog_card.pack(fill='x', pady=(0, 12))

        prog_top = tk.Frame(prog_card, bg=CARD)
        prog_top.pack(fill='x')

        self._pct_var  = tk.StringVar(value='0%')
        self._eta_var  = tk.StringVar(value='')
        tk.Label(prog_top, textvariable=self._pct_var,
                 bg=CARD, fg=TEXT, font=('Segoe UI', 10, 'bold')).pack(side='left')
        tk.Label(prog_top, textvariable=self._eta_var,
                 bg=CARD, fg=SUBTEXT, font=('Segoe UI', 9)).pack(side='right')

        self._prog_var = tk.DoubleVar(value=0)
        ttk.Progressbar(prog_card, variable=self._prog_var,
                        maximum=100, style='TProgressbar').pack(fill='x', pady=(8, 0))

        # ── Download button ───────────────────────────────────────────────────
        self._dl_btn = ttk.Button(outer, text='Download',
                                  style='Accent.TButton',
                                  state='disabled', command=self._download)
        self._dl_btn.pack(fill='x', pady=(0, 12), ipady=4)

        # ── Log ───────────────────────────────────────────────────────────────
        log_frame = ttk.Frame(outer, style='Card.TFrame')
        log_frame.pack(fill='both', expand=True)

        tk.Label(log_frame, text='Log', font=('Segoe UI', 9, 'bold'),
                 bg=CARD, fg=SUBTEXT, padx=10, pady=6).pack(anchor='w')

        txt_wrap = tk.Frame(log_frame, bg=CARD)
        txt_wrap.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        self._log_text = tk.Text(txt_wrap, height=6,
                                  bg=BG, fg=TEXT, insertbackground=TEXT,
                                  relief='flat', font=FONT_MONO,
                                  state='disabled', wrap='word')
        sb = ttk.Scrollbar(txt_wrap, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self._log_text.pack(fill='both', expand=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self._log_q.put(msg)

    def _poll_log(self):
        while not self._log_q.empty():
            msg = self._log_q.get_nowait()
            self._log_text.configure(state='normal')
            self._log_text.insert('end', msg + '\n')
            self._log_text.see('end')
            self._log_text.configure(state='disabled')
        self.root.after(120, self._poll_log)

    def _browse(self):
        folder = filedialog.askdirectory(title='Select output folder')
        if folder:
            self._out_var.set(folder)

    def _get_ydl_opts(self, **extra):
        opts = {
            'quiet':            True,
            'no_warnings':      False,
            'ignoreerrors':     False,
        }
        if os.path.isfile(_NODE):
            opts['js_runtimes'] = f'node:{_NODE}'
            opts['remote_components'] = 'ejs:github'
        if os.path.isfile(_FFMPEG):
            opts['ffmpeg_location'] = os.path.dirname(_FFMPEG)
        opts.update(extra)
        return opts

    def _make_logger(self):
        app = self
        class _Logger:
            def debug(self, msg):
                if not msg.startswith('[debug]'):
                    app._log(msg)
            def info(self, msg):
                app._log(msg)
            def warning(self, msg):
                app._log(f'⚠  {msg}')
            def error(self, msg):
                app._log(f'✖  {msg}')
        return _Logger()

    def _update_fmt_hint(self, *_):
        if self._fd is None:
            return
        height = self._selected_height()
        lang   = self._selected_lang()
        if height and lang:
            fmt = make_format_string(height, lang, self._fd)
            self._fmt_hint.set(f'yt-dlp format: {fmt}')

    def _selected_height(self) -> int | None:
        label = self._quality_var.get()
        for h, l in RESOLUTION_LABELS.items():
            if l == label:
                return h
        try:
            return int(label.split('p')[0])
        except Exception:
            return None

    def _selected_lang(self) -> str | None:
        val = self._lang_var.get()
        if '(' in val:
            return val.rsplit('(', 1)[-1].rstrip(')')
        return None

    # ── Fetch ─────────────────────────────────────────────────────────────────

    def _fetch(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showerror('MediaGrab', 'Please enter a video URL.', parent=self.root)
            return

        self._fetch_btn.configure(state='disabled', text='Fetching…')
        self._dl_btn.configure(state='disabled')
        self._title_var.set('Contacting server…')
        self._log(f'→ Fetching: {url}')

        def _work():
            try:
                opts = self._get_ydl_opts(logger=self._make_logger())
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                fd = parse_info(info)
                self.root.after(0, lambda: self._on_fetch_ok(fd))
            except Exception as exc:
                self.root.after(0, lambda: self._on_fetch_err(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _on_fetch_ok(self, fd: FormatData):
        self._fd = fd
        self._fetch_btn.configure(state='normal', text='Fetch Formats')

        dur = fd.duration or 0
        m, s = divmod(int(dur), 60)
        h, m = divmod(m, 60)
        dur_str = f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'
        self._title_var.set(f'{fd.title}  ({dur_str})')
        self._log(f'✔ Title: {fd.title}')
        self._log(f'  Resolutions : {", ".join(RESOLUTION_LABELS.get(r, f"{r}p") for r in fd.resolutions)}')
        self._log(f'  Audio langs : {", ".join(fd.languages.values())}')

        # Populate quality combo (highest first)
        res_labels = [RESOLUTION_LABELS.get(r, f'{r}p') for r in reversed(fd.resolutions)]
        self._quality_cb['values'] = res_labels
        self._quality_cb.configure(state='readonly')
        self._quality_var.set(res_labels[0] if res_labels else '—')

        # Populate language combo (sort by name)
        lang_labels = [
            f'{name}  ({code})'
            for code, name in sorted(fd.languages.items(), key=lambda kv: kv[1])
        ]
        self._lang_cb['values'] = lang_labels
        self._lang_cb.configure(state='readonly')
        # default: prefer English (US/original), else first
        default_lang = next(
            (l for l in lang_labels if 'English' in l),
            lang_labels[0] if lang_labels else '—'
        )
        self._lang_var.set(default_lang)

        self._dl_btn.configure(state='normal')
        self._update_fmt_hint()

    def _on_fetch_err(self, exc: Exception):
        self._fetch_btn.configure(state='normal', text='Fetch Formats')
        self._title_var.set('Failed to fetch — check the URL and your internet connection.')
        self._log(f'✖  {exc}')
        messagebox.showerror('MediaGrab', f'Could not fetch formats:\n{exc}', parent=self.root)

    # ── Download ──────────────────────────────────────────────────────────────

    def _download(self):
        if self._fd is None:
            return

        url    = self._url_var.get().strip()
        outdir = self._out_var.get().strip()
        height = self._selected_height()
        lang   = self._selected_lang()

        if not outdir or not os.path.isdir(outdir):
            messagebox.showerror('MediaGrab', 'Please choose a valid output folder.', parent=self.root)
            return
        if not height or not lang:
            messagebox.showerror('MediaGrab', 'Please select quality and audio language.', parent=self.root)
            return

        fmt = make_format_string(height, lang, self._fd)
        self._log(f'→ Downloading  quality={RESOLUTION_LABELS.get(height, f"{height}p")}  lang={lang}  fmt={fmt}')

        self._dl_btn.configure(state='disabled', text='Downloading…')
        self._prog_var.set(0)
        self._pct_var.set('0%')
        self._eta_var.set('')

        def _update_progress(pct, info):
            self._prog_var.set(max(pct, 0))
            self._pct_var.set(f'{max(pct, 0):.1f}%')
            self._eta_var.set(info)

        def _work():
            try:
                def _prog_cb(pct, info):
                    self.root.after(0, lambda: _update_progress(pct, info))

                outtmpl = os.path.join(outdir, '%(title)s [%(language)s].%(ext)s')

                opts = self._get_ydl_opts(
                    format=fmt,
                    outtmpl=outtmpl,
                    merge_output_format='mp4',
                    progress_hooks=[make_progress_hook(_prog_cb)],
                    logger=self._make_logger(),
                    quiet=False,
                    postprocessors=[{
                        'key': 'FFmpegVideoConvertor',
                        'preferedformat': 'mp4',
                    }],
                )

                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])

                self.root.after(0, self._on_dl_ok)
            except Exception as exc:
                self.root.after(0, lambda: self._on_dl_err(exc))

        self._dl_thread = threading.Thread(target=_work, daemon=True)
        self._dl_thread.start()

    def _on_dl_ok(self):
        self._prog_var.set(100)
        self._pct_var.set('100%')
        self._eta_var.set('Done!')
        self._dl_btn.configure(state='normal', text='Download')
        self._log('✔ Download complete!')
        messagebox.showinfo('MediaGrab', 'Download completed successfully!', parent=self.root)

    def _on_dl_err(self, exc: Exception):
        self._dl_btn.configure(state='normal', text='Download')
        self._pct_var.set('Error')
        self._eta_var.set('')
        self._log(f'✖  {exc}')
        messagebox.showerror('MediaGrab', f'Download failed:\n{exc}', parent=self.root)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    app = MediaGrabApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
