"""
MediaGrab Mobile — Android Video Downloader
Kivy + KivyMD + yt-dlp

Desktop test : .venv/Scripts/python.exe app_mobile.py
Build APK    : push to GitHub → Actions builds it automatically
"""
import os, json, threading
from datetime import datetime

# ── Android detection ──────────────────────────────────────────────────────────
try:
    from android.permissions import request_permissions, Permission
    from android.storage import primary_external_storage_path
    IS_ANDROID = True
except ImportError:
    IS_ANDROID = False

# ── Phone-sized window for desktop testing ─────────────────────────────────────
from kivy.config import Config
if not IS_ANDROID:
    Config.set('graphics', 'width',  '400')
    Config.set('graphics', 'height', '800')
    Config.set('graphics', 'resizable', '1')

from kivy.lang import Builder
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import ListProperty

from kivymd.app import MDApp
from kivymd.uix.bottomnavigation import MDBottomNavigation, MDBottomNavigationItem
from kivymd.uix.textfield import MDTextField
from kivymd.uix.button import (MDRaisedButton, MDFlatButton,
                                MDFillRoundFlatButton, MDRoundFlatButton)
from kivymd.uix.label import MDLabel
from kivymd.uix.progressbar import MDProgressBar
from kivymd.uix.list import MDList, TwoLineListItem, OneLineListItem
from kivymd.uix.snackbar import MDSnackbar
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.dropdownitem import MDDropDownItem
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.card import MDCard

import yt_dlp

# ── Paths ──────────────────────────────────────────────────────────────────────
if IS_ANDROID:
    _SAVE_DIR = os.path.join(primary_external_storage_path(), 'Download', 'MediaGrab')
    _DATA_DIR = os.path.join(primary_external_storage_path(), 'Android', 'data', 'mediagrab')
else:
    _SAVE_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', 'MediaGrab')
    _DATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'MediaGrab')

_LIBRARY_F  = os.path.join(_DATA_DIR, 'library.json')
_SETTINGS_F = os.path.join(_DATA_DIR, 'settings.json')
_NODE       = r'C:\Program Files\nodejs\node.exe'
_FFMPEG     = os.path.join(os.path.dirname(os.path.abspath(__file__)),
              '..', 'ffmpeg-2026-05-18-git-b4d11dffbf-essentials_build', 'bin', 'ffmpeg.exe')

os.makedirs(_DATA_DIR, exist_ok=True)
os.makedirs(_SAVE_DIR, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────────
THEMES = {
    'Red':    [0.88, 0.10, 0.08, 1],
    'Purple': [0.47, 0.20, 0.90, 1],
    'Blue':   [0.12, 0.37, 0.92, 1],
    'Teal':   [0.02, 0.56, 0.52, 1],
}

RES_LABELS = {
    4320: '8K (4320p)', 2160: '4K Ultra HD', 1440: '2K (1440p)',
    1080: '1080p Full HD', 720: '720p HD',
    480: '480p SD', 360: '360p', 240: '240p', 144: '144p',
}

KNOWN_LANGS = {
    'en': 'English', 'ta': 'Tamil', 'hi': 'Hindi',
    'te': 'Telugu', 'ml': 'Malayalam', 'kn': 'Kannada',
    'bn': 'Bengali', 'ar': 'Arabic', 'fr': 'French',
    'de': 'German', 'es': 'Spanish', 'ja': 'Japanese',
    'ko': 'Korean', 'zh-Hans': 'Chinese (Simplified)',
    'ru': 'Russian', 'tr': 'Turkish', 'id': 'Indonesian',
    'pt-BR': 'Portuguese (BR)', 'it': 'Italian',
}

FORMATS = ['Video (MP4)', 'Audio MP3', 'Audio WAV']

# ── FormatData ─────────────────────────────────────────────────────────────────
class FormatData:
    def __init__(self):
        self.title = ''; self.channel = ''; self.duration = 0
        self.resolutions = []; self.languages = {}; self.hls_map = {}

def parse_info(info):
    fd = FormatData()
    fd.title    = info.get('title', '')
    fd.channel  = info.get('channel', '') or info.get('uploader', '')
    fd.duration = info.get('duration', 0) or 0
    res_set, lang_map = set(), {}
    for f in info.get('formats', []):
        h     = f.get('height')
        lang  = f.get('language')
        hv    = f.get('vcodec', 'none') not in (None, 'none')
        ha    = f.get('acodec', 'none') not in (None, 'none')
        proto = f.get('protocol', '')
        if hv and ha and 'm3u8' in proto and h and lang:
            res_set.add(h)
            lang_map.setdefault(lang, KNOWN_LANGS.get(lang, lang))
            fd.hls_map.setdefault((h, lang), f['format_id'])
        if hv and not ha and h:
            res_set.add(h)
        if not hv and ha and lang:
            lang_map.setdefault(lang, KNOWN_LANGS.get(lang, lang))
    fd.resolutions = sorted(res_set, reverse=True)
    fd.languages   = lang_map
    return fd

def make_fmt(h, lang, fd):
    if (h, lang) in fd.hls_map:
        return fd.hls_map[(h, lang)]
    return (f'bestvideo[height<={h}][ext=mp4]/bestvideo[height<={h}]'
            f'+bestaudio[language={lang}]/bestaudio')

def make_audio_fmt(lang, fd):
    best_h = max((k[0] for k in fd.hls_map if k[1] == lang), default=None)
    if best_h is not None:
        return fd.hls_map[(best_h, lang)]
    return f'bestaudio[language={lang}]/bestaudio'

def fmt_dur(s):
    if not s: return ''
    m, s = divmod(int(s), 60); h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

def fmt_speed(bps):
    if not bps: return ''
    for u in ('B/s', 'KB/s', 'MB/s', 'GB/s'):
        if bps < 1024: return f'{bps:.0f} {u}'
        bps /= 1024
    return f'{bps:.1f} GB/s'

# ── Persistence ────────────────────────────────────────────────────────────────
def load_settings():
    try:
        with open(_SETTINGS_F) as f: return json.load(f)
    except: return {'theme': 'Red', 'save_dir': _SAVE_DIR}

def save_settings_file(s):
    try:
        with open(_SETTINGS_F, 'w') as f: json.dump(s, f, indent=2)
    except: pass

def load_library():
    try:
        with open(_LIBRARY_F) as f: return json.load(f)
    except: return []

def add_to_library(entry):
    lib = load_library()
    lib.insert(0, entry)
    try:
        with open(_LIBRARY_F, 'w') as f: json.dump(lib[:500], f, indent=2)
    except: pass

# ── yt-dlp helpers ─────────────────────────────────────────────────────────────
def make_ydl_opts(skip_hls=False, **kw):
    o = {'quiet': True, 'no_warnings': True}
    if not IS_ANDROID:
        if os.path.isfile(_NODE):
            o['js_runtimes']       = {'node': {'path': _NODE}}
            o['remote_components'] = ['ejs:github']
        if os.path.isfile(_FFMPEG):
            o['ffmpeg_location'] = os.path.dirname(_FFMPEG)
    if skip_hls:
        o['extractor_args'] = {'youtube': {'skip': ['hls']}}
    o.update(kw)
    return o


# ══════════════════════════════════════════════════════════════════════════════
#  KV — Premium dark UI
# ══════════════════════════════════════════════════════════════════════════════
KV = r'''
#:import dp kivy.metrics.dp

# ── Shared card ────────────────────────────────────────────────────────────────
<MCard@MDCard>:
    elevation: 0
    radius: [dp(22)]
    padding: dp(20)
    md_bg_color: app.card_color
    orientation: "vertical"
    spacing: dp(14)
    adaptive_height: True

# ── Small ALL-CAPS section label ───────────────────────────────────────────────
<SLabel@MDLabel>:
    font_style: "Overline"
    theme_text_color: "Secondary"
    adaptive_height: True

# ═══════════════════════════════════════════════════════════════════════════════
#  DOWNLOAD TAB
# ═══════════════════════════════════════════════════════════════════════════════
<DownloadTab>:
    name: "download"
    MDScrollView:
        MDBoxLayout:
            orientation: "vertical"
            adaptive_height: True

            # ── BIG coloured header ───────────────────────────────────────────
            MDBoxLayout:
                orientation: "vertical"
                size_hint_y: None
                height: dp(196)
                padding: [dp(24), dp(46), dp(24), dp(22)]
                spacing: dp(6)
                md_bg_color: app.accent_color
                radius: [0, 0, dp(40), dp(40)]

                MDLabel:
                    text: "MediaGrab"
                    font_style: "H3"
                    bold: True
                    theme_text_color: "Custom"
                    text_color: 1, 1, 1, 1
                    size_hint_y: None
                    height: dp(68)

                MDLabel:
                    text: "Download any video · any language · any quality"
                    font_style: "Body1"
                    theme_text_color: "Custom"
                    text_color: 1, 1, 1, 0.82
                    adaptive_height: True

            # ── Cards ─────────────────────────────────────────────────────────
            MDBoxLayout:
                orientation: "vertical"
                adaptive_height: True
                padding: [dp(16), dp(22), dp(16), dp(100)]
                spacing: dp(16)

                # ── URL INPUT ─────────────────────────────────────────────────
                MCard:
                    SLabel:
                        text: "VIDEO URL"
                    MDTextField:
                        id: url_field
                        hint_text: "Paste YouTube, Instagram, TikTok…"
                        mode: "rectangle"
                        adaptive_height: True
                        multiline: False
                        on_text_validate: root.fetch()
                    MDBoxLayout:
                        adaptive_height: True
                        spacing: dp(10)
                        MDFillRoundFlatButton:
                            id: fetch_btn
                            text: "FETCH INFO"
                            md_bg_color: app.accent_color
                            size_hint_x: 1
                            height: dp(52)
                            on_release: root.fetch()
                        MDRoundFlatButton:
                            text: "CLEAR"
                            size_hint_x: None
                            width: dp(84)
                            height: dp(52)
                            line_color: 0.38, 0.38, 0.38, 1
                            on_release: root.clear_all()

                # ── VIDEO INFO (hidden) ────────────────────────────────────────
                MCard:
                    id: info_card
                    opacity: 0
                    size_hint_y: None
                    height: 0
                    MDBoxLayout:
                        adaptive_height: True
                        spacing: dp(16)

                        # Thumbnail box
                        MDBoxLayout:
                            size_hint_x: None
                            width: dp(76)
                            size_hint_y: None
                            height: dp(76)
                            md_bg_color: app.accent_color
                            radius: [dp(16)]
                            MDLabel:
                                text: "▶"
                                font_style: "H5"
                                theme_text_color: "Custom"
                                text_color: 1, 1, 1, 1
                                halign: "center"
                                valign: "middle"
                                text_size: dp(76), dp(76)
                                size_hint_y: None
                                height: dp(76)

                        # Title / channel / duration
                        MDBoxLayout:
                            orientation: "vertical"
                            adaptive_height: True
                            spacing: dp(5)
                            MDLabel:
                                id: title_lbl
                                text: ""
                                font_style: "Subtitle1"
                                bold: True
                                adaptive_height: True
                                text_size: self.width, None
                            MDLabel:
                                id: channel_lbl
                                text: ""
                                font_style: "Caption"
                                theme_text_color: "Secondary"
                                adaptive_height: True
                            MDLabel:
                                id: dur_lbl
                                text: ""
                                font_style: "Caption"
                                theme_text_color: "Custom"
                                text_color: app.accent_color
                                adaptive_height: True

                # ── OPTIONS (hidden) ──────────────────────────────────────────
                MCard:
                    id: options_card
                    opacity: 0
                    size_hint_y: None
                    height: 0

                    SLabel:
                        text: "FORMAT"

                    # Pill toggle chips
                    MDBoxLayout:
                        adaptive_height: True
                        spacing: dp(8)
                        MDFillRoundFlatButton:
                            id: fmt_video_btn
                            text: "VIDEO"
                            size_hint_x: 1
                            height: dp(46)
                            md_bg_color: app.accent_color
                            on_release: root.set_fmt("Video (MP4)")
                        MDFillRoundFlatButton:
                            id: fmt_mp3_btn
                            text: "MP3"
                            size_hint_x: 1
                            height: dp(46)
                            md_bg_color: 0.20, 0.20, 0.20, 1
                            on_release: root.set_fmt("Audio MP3")
                        MDFillRoundFlatButton:
                            id: fmt_wav_btn
                            text: "WAV"
                            size_hint_x: 1
                            height: dp(46)
                            md_bg_color: 0.20, 0.20, 0.20, 1
                            on_release: root.set_fmt("Audio WAV")

                    # Divider
                    MDBoxLayout:
                        size_hint_y: None
                        height: dp(1)
                        md_bg_color: 0.26, 0.26, 0.26, 1

                    SLabel:
                        text: "QUALITY & LANGUAGE"

                    # Outlined pill selectors
                    MDBoxLayout:
                        adaptive_height: True
                        spacing: dp(10)
                        MDRoundFlatButton:
                            id: quality_dd
                            text: "1080p Full HD"
                            size_hint_x: 1
                            height: dp(48)
                            line_color: app.accent_color
                            on_release: root.open_quality_menu(self)
                        MDRoundFlatButton:
                            id: lang_dd
                            text: "Language"
                            size_hint_x: 1
                            height: dp(48)
                            line_color: app.accent_color
                            on_release: root.open_lang_menu(self)

                # ── DOWNLOAD (hidden) ─────────────────────────────────────────
                MCard:
                    id: dl_card
                    opacity: 0
                    size_hint_y: None
                    height: 0

                    MDBoxLayout:
                        adaptive_height: True
                        MDLabel:
                            id: prog_lbl
                            text: "Ready"
                            font_style: "Body2"
                            theme_text_color: "Secondary"
                            adaptive_height: True
                        MDLabel:
                            id: speed_lbl
                            text: ""
                            font_style: "Body2"
                            theme_text_color: "Custom"
                            text_color: app.accent_color
                            adaptive_height: True
                            halign: "right"

                    MDProgressBar:
                        id: prog_bar
                        value: 0
                        color: app.accent_color
                        size_hint_y: None
                        height: dp(10)

                    MDFillRoundFlatButton:
                        id: dl_btn
                        text: "DOWNLOAD NOW"
                        md_bg_color: app.accent_color
                        size_hint_x: 1
                        height: dp(60)
                        disabled: True
                        on_release: root.start_download()

# ═══════════════════════════════════════════════════════════════════════════════
#  QUEUE TAB
# ═══════════════════════════════════════════════════════════════════════════════
<QueueTab>:
    name: "queue"
    MDBoxLayout:
        orientation: "vertical"

        MDBoxLayout:
            size_hint_y: None
            height: dp(120)
            padding: [dp(24), dp(42), dp(24), dp(18)]
            md_bg_color: app.accent_color
            radius: [0, 0, dp(40), dp(40)]
            MDLabel:
                text: "Queue"
                font_style: "H4"
                bold: True
                theme_text_color: "Custom"
                text_color: 1, 1, 1, 1

        MDBoxLayout:
            orientation: "vertical"
            padding: [dp(16), dp(16), dp(16), dp(90)]
            spacing: dp(14)

            MCard:
                MDTextField:
                    id: q_url
                    hint_text: "Paste URL to queue…"
                    mode: "rectangle"
                    adaptive_height: True
                    multiline: False
                MDBoxLayout:
                    adaptive_height: True
                    spacing: dp(6)
                    MDDropDownItem:
                        id: q_quality_dd
                        text: "1080p Full HD"
                        size_hint_x: 1
                        on_release: root.open_quality_menu(self)
                    MDDropDownItem:
                        id: q_lang_dd
                        text: "Tamil"
                        size_hint_x: 1
                        on_release: root.open_lang_menu(self)
                    MDDropDownItem:
                        id: q_fmt_dd
                        text: "MP4"
                        size_hint_x: 1
                        on_release: root.open_fmt_menu(self)
                MDFillRoundFlatButton:
                    text: "+ ADD TO QUEUE"
                    md_bg_color: app.accent_color
                    size_hint_x: 1
                    height: dp(50)
                    on_release: root.add_item()

            MDScrollView:
                MDList:
                    id: queue_list

            MCard:
                MDProgressBar:
                    id: q_prog
                    value: 0
                    color: app.accent_color
                    size_hint_y: None
                    height: dp(10)
                MDLabel:
                    id: q_status
                    text: "Queue ready"
                    font_style: "Caption"
                    theme_text_color: "Secondary"
                    adaptive_height: True
                MDBoxLayout:
                    adaptive_height: True
                    spacing: dp(8)
                    MDFillRoundFlatButton:
                        id: q_start_btn
                        text: "START"
                        md_bg_color: app.accent_color
                        size_hint_x: 1
                        height: dp(50)
                        on_release: root.start_queue()
                    MDRoundFlatButton:
                        text: "STOP"
                        height: dp(50)
                        line_color: 0.38, 0.38, 0.38, 1
                        on_release: root.stop_queue()
                    MDRoundFlatButton:
                        text: "CLEAR"
                        height: dp(50)
                        line_color: 0.38, 0.38, 0.38, 1
                        on_release: root.clear_done()

# ═══════════════════════════════════════════════════════════════════════════════
#  LIBRARY TAB
# ═══════════════════════════════════════════════════════════════════════════════
<LibraryTab>:
    name: "library"
    MDBoxLayout:
        orientation: "vertical"

        MDBoxLayout:
            size_hint_y: None
            height: dp(120)
            padding: [dp(24), dp(42), dp(14), dp(18)]
            md_bg_color: app.accent_color
            radius: [0, 0, dp(40), dp(40)]
            MDLabel:
                text: "Library"
                font_style: "H4"
                bold: True
                theme_text_color: "Custom"
                text_color: 1, 1, 1, 1
                size_hint_x: 1
            MDFlatButton:
                text: "CLEAR ALL"
                theme_text_color: "Custom"
                text_color: 1, 1, 1, 0.85
                size_hint_x: None
                width: dp(96)
                on_release: root.clear_history()

        MDScrollView:
            MDList:
                id: lib_list

# ═══════════════════════════════════════════════════════════════════════════════
#  SETTINGS TAB
# ═══════════════════════════════════════════════════════════════════════════════
<SettingsTab>:
    name: "settings"
    MDScrollView:
        MDBoxLayout:
            orientation: "vertical"
            adaptive_height: True

            MDBoxLayout:
                size_hint_y: None
                height: dp(120)
                padding: [dp(24), dp(42), dp(24), dp(18)]
                md_bg_color: app.accent_color
                radius: [0, 0, dp(40), dp(40)]
                MDLabel:
                    text: "Settings"
                    font_style: "H4"
                    bold: True
                    theme_text_color: "Custom"
                    text_color: 1, 1, 1, 1

            MDBoxLayout:
                orientation: "vertical"
                adaptive_height: True
                padding: [dp(16), dp(22), dp(16), dp(100)]
                spacing: dp(16)

                # ── Color theme ───────────────────────────────────────────────
                MCard:
                    SLabel:
                        text: "COLOR THEME"
                    MDBoxLayout:
                        adaptive_height: True
                        spacing: dp(10)
                        MDRaisedButton:
                            text: "Red"
                            size_hint_x: 1
                            height: dp(52)
                            md_bg_color: 0.88, 0.10, 0.08, 1
                            on_release: app.set_theme("Red")
                        MDRaisedButton:
                            text: "Purple"
                            size_hint_x: 1
                            height: dp(52)
                            md_bg_color: 0.47, 0.20, 0.90, 1
                            on_release: app.set_theme("Purple")
                        MDRaisedButton:
                            text: "Blue"
                            size_hint_x: 1
                            height: dp(52)
                            md_bg_color: 0.12, 0.37, 0.92, 1
                            on_release: app.set_theme("Blue")
                        MDRaisedButton:
                            text: "Teal"
                            size_hint_x: 1
                            height: dp(52)
                            md_bg_color: 0.02, 0.56, 0.52, 1
                            on_release: app.set_theme("Teal")

                # ── Save folder ───────────────────────────────────────────────
                MCard:
                    SLabel:
                        text: "SAVE FOLDER"
                    MDLabel:
                        id: save_dir_lbl
                        text: ""
                        font_style: "Body2"
                        theme_text_color: "Secondary"
                        adaptive_height: True
                        text_size: self.width, None
                    MDFillRoundFlatButton:
                        text: "OPEN FOLDER"
                        md_bg_color: app.accent_color
                        size_hint_x: 1
                        height: dp(50)
                        on_release: root.open_folder()

                # ── About ─────────────────────────────────────────────────────
                MCard:
                    SLabel:
                        text: "ABOUT"
                    MDLabel:
                        text: "MediaGrab  v3.0"
                        font_style: "H6"
                        bold: True
                        adaptive_height: True
                    MDBoxLayout:
                        size_hint_y: None
                        height: dp(1)
                        md_bg_color: 0.26, 0.26, 0.26, 1
                    MDLabel:
                        text: "Personal video downloader\nYouTube · Instagram · Twitter · 1000+ sites\n4K · 8K · All audio languages\nMP4 · MP3 · WAV"
                        font_style: "Body2"
                        theme_text_color: "Secondary"
                        adaptive_height: True
'''


# ══════════════════════════════════════════════════════════════════════════════
#  Download Tab
# ══════════════════════════════════════════════════════════════════════════════
class DownloadTab(MDBottomNavigationItem):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._fd           = None
        self._sel_quality  = 1080
        self._sel_lang     = 'ta'
        self._sel_fmt      = 'Video (MP4)'
        self._quality_menu = None
        self._lang_menu    = None

    def snack(self, msg):
        MDSnackbar(MDLabel(text=msg)).open()

    def _show_card(self, card_id, show):
        c = self.ids[card_id]
        if show:
            c.opacity     = 1
            c.size_hint_y = None
            c.height      = c.minimum_height
        else:
            c.opacity     = 0
            c.size_hint_y = None
            c.height      = 0

    def set_fmt(self, fmt):
        self._sel_fmt = fmt
        acc  = list(MDApp.get_running_app().accent_color)
        dark = [0.20, 0.20, 0.20, 1]
        self.ids.fmt_video_btn.md_bg_color = acc  if fmt == 'Video (MP4)' else dark
        self.ids.fmt_mp3_btn.md_bg_color   = acc  if fmt == 'Audio MP3'   else dark
        self.ids.fmt_wav_btn.md_bg_color   = acc  if fmt == 'Audio WAV'   else dark

    def clear_all(self):
        self.ids.url_field.text = ''
        self._fd = None
        for cid in ('info_card', 'options_card', 'dl_card'):
            self._show_card(cid, False)
        self.ids.prog_bar.value  = 0
        self.ids.prog_lbl.text   = 'Ready'
        self.ids.speed_lbl.text  = ''
        self.ids.dl_btn.disabled = True
        self.ids.dl_btn.text     = 'DOWNLOAD NOW'

    def fetch(self):
        url = self.ids.url_field.text.strip()
        if not url:
            self.snack('Paste a URL first.')
            return
        self.ids.fetch_btn.disabled = True
        self.ids.fetch_btn.text     = 'Fetching…'
        self.ids.dl_btn.disabled    = True
        for cid in ('info_card', 'options_card', 'dl_card'):
            self._show_card(cid, False)
        threading.Thread(target=self._do_fetch, args=(url,), daemon=True).start()

    def _do_fetch(self, url):
        try:
            opts = make_ydl_opts(skip_hls=True)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            fd = parse_info(info)
            Clock.schedule_once(lambda dt: self._on_fetch_p1(fd, url))
        except Exception as e:
            Clock.schedule_once(lambda dt, err=str(e): self._on_fetch_err(err))

    def _on_fetch_p1(self, fd, url):
        self._fd = fd
        self.ids.title_lbl.text   = fd.title
        self.ids.channel_lbl.text = fd.channel
        self.ids.dur_lbl.text     = fmt_dur(fd.duration)
        self._show_card('info_card', True)
        self._show_card('dl_card',   True)
        self.ids.dl_btn.text     = 'Loading…'
        self.ids.dl_btn.disabled = True
        threading.Thread(target=self._do_fetch_p2, args=(url,), daemon=True).start()

    def _do_fetch_p2(self, url):
        try:
            opts = make_ydl_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            fd = parse_info(info)
            Clock.schedule_once(lambda dt: self._on_fetch_p2(fd))
        except Exception as e:
            Clock.schedule_once(lambda dt, err=str(e): self.snack(f'Audio load failed: {err[:60]}'))

    def _on_fetch_p2(self, fd):
        self._fd = fd
        self._populate_quality(fd)
        self._populate_lang(fd)
        self._show_card('options_card', True)
        self.ids.fetch_btn.disabled = False
        self.ids.fetch_btn.text     = 'FETCH INFO'
        self.ids.dl_btn.disabled    = False
        self.ids.dl_btn.text        = 'DOWNLOAD NOW'
        self.snack(f'{len(fd.languages)} audio languages · {len(fd.resolutions)} resolutions')

    def _on_fetch_err(self, err):
        self.ids.fetch_btn.disabled = False
        self.ids.fetch_btn.text     = 'FETCH INFO'
        self.snack(f'Error: {err[:80]}')

    def _populate_quality(self, fd):
        labels = [RES_LABELS.get(r, f'{r}p') for r in fd.resolutions]
        if not labels:
            labels = ['1080p Full HD', '720p HD', '480p SD']
        self.ids.quality_dd.text = labels[0]
        self._sel_quality  = fd.resolutions[0] if fd.resolutions else 1080
        self._quality_items = list(zip(fd.resolutions, labels))

    def _populate_lang(self, fd):
        items = list(fd.languages.items())
        if not items:
            items = [('en', 'English')]
        self._lang_items = items
        default = next(((c, n) for c, n in items if c == 'ta'), items[0])
        self._sel_lang      = default[0]
        self.ids.lang_dd.text = default[1]

    def open_quality_menu(self, caller):
        items = getattr(self, '_quality_items', [(1080, '1080p Full HD'), (720, '720p HD')])
        menu_items = [
            {'text': label,
             'on_release': lambda h=h, l=label: self._set_quality(caller, h, l)}
            for h, label in items
        ]
        if self._quality_menu:
            self._quality_menu.dismiss()
        self._quality_menu = MDDropdownMenu(caller=caller, items=menu_items, width_mult=4)
        self._quality_menu.open()

    def _set_quality(self, caller, h, label):
        self._sel_quality = h
        caller.text = label
        if self._quality_menu:
            self._quality_menu.dismiss()

    def open_lang_menu(self, caller):
        items = getattr(self, '_lang_items', list(KNOWN_LANGS.items()))
        menu_items = [
            {'text': name,
             'on_release': lambda c=code, n=name: self._set_lang(caller, c, n)}
            for code, name in items
        ]
        if self._lang_menu:
            self._lang_menu.dismiss()
        self._lang_menu = MDDropdownMenu(caller=caller, items=menu_items, width_mult=4)
        self._lang_menu.open()

    def _set_lang(self, caller, code, name):
        self._sel_lang  = code
        caller.text     = name
        if self._lang_menu:
            self._lang_menu.dismiss()

    def start_download(self):
        if not self._fd:
            return
        url        = self.ids.url_field.text.strip()
        outdir     = MDApp.get_running_app().settings.get('save_dir', _SAVE_DIR)
        lang       = self._sel_lang
        h          = self._sel_quality
        dl_fmt     = self._sel_fmt

        if dl_fmt == 'Audio MP3':
            fmt, audio_codec = make_audio_fmt(lang, self._fd), 'mp3'
        elif dl_fmt == 'Audio WAV':
            fmt, audio_codec = make_audio_fmt(lang, self._fd), 'wav'
        else:
            fmt, audio_codec = make_fmt(h, lang, self._fd), None

        self.ids.dl_btn.disabled = True
        self.ids.dl_btn.text     = 'Downloading…'
        self.ids.prog_bar.value  = 0
        self.ids.prog_lbl.text   = '0%'

        fd_snap = self._fd
        threading.Thread(
            target=self._do_download,
            args=(url, fmt, outdir, audio_codec, fd_snap, lang, dl_fmt, h),
            daemon=True
        ).start()

    def _do_download(self, url, fmt, outdir, audio_codec, fd, lang, dl_fmt, h):
        def _hook(d):
            if d.get('status') == 'downloading':
                dl    = d.get('downloaded_bytes', 0) or 0
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                fi, fc = d.get('fragment_index', 0) or 0, d.get('fragment_count', 0) or 0
                pct = (dl / total * 100) if total else ((fi / fc * 100) if fc else 0)
                pct = min(pct, 99)
                spd = fmt_speed(d.get('speed'))
                Clock.schedule_once(lambda dt, p=pct, sp=spd: (
                    setattr(self.ids.prog_bar, 'value', p),
                    setattr(self.ids.prog_lbl, 'text', f'{p:.0f}%'),
                    setattr(self.ids.speed_lbl, 'text', sp),
                ))
        try:
            extra = {}
            if audio_codec:
                pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': audio_codec}
                if audio_codec == 'mp3':
                    pp['preferredquality'] = '320'
                extra['postprocessors'] = [pp]
            else:
                extra['merge_output_format'] = 'mp4'
            opts = make_ydl_opts(
                format=fmt,
                outtmpl=os.path.join(outdir, '%(title)s [%(language)s].%(ext)s'),
                progress_hooks=[_hook], **extra)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            add_to_library({
                'title':    fd.title,
                'language': lang,
                'quality':  RES_LABELS.get(h, f'{h}p') if h else dl_fmt,
                'format':   dl_fmt,
                'dir':      outdir,
                'date':     datetime.now().strftime('%Y-%m-%d %H:%M'),
            })
            Clock.schedule_once(lambda dt: self._on_done(True))
        except Exception as e:
            Clock.schedule_once(lambda dt, err=str(e): self._on_done(False, err))

    def _on_done(self, ok, err=''):
        self.ids.dl_btn.disabled = False
        self.ids.dl_btn.text     = 'DOWNLOAD NOW'
        self.ids.prog_bar.value  = 100 if ok else 0
        self.ids.prog_lbl.text   = 'Done!' if ok else 'Error'
        self.ids.speed_lbl.text  = ''
        self.snack('Download complete!' if ok else f'Failed: {err[:60]}')


# ══════════════════════════════════════════════════════════════════════════════
#  Queue Tab
# ══════════════════════════════════════════════════════════════════════════════
class QueueTab(MDBottomNavigationItem):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._items        = []
        self._running      = False
        self._stop         = False
        self._q_quality    = 1080
        self._q_lang       = 'ta'
        self._q_fmt        = 'Video (MP4)'
        self._quality_menu = self._lang_menu = self._fmt_menu = None

    def snack(self, msg):
        MDSnackbar(MDLabel(text=msg)).open()

    def open_quality_menu(self, caller):
        items = [{'text': l, 'on_release': lambda h=h, l=l: self._set_q(caller, 'quality', h, l)}
                 for h, l in RES_LABELS.items()]
        if self._quality_menu: self._quality_menu.dismiss()
        self._quality_menu = MDDropdownMenu(caller=caller, items=items, width_mult=4)
        self._quality_menu.open()

    def _set_q(self, caller, field, val, label):
        if field == 'quality': self._q_quality = val
        elif field == 'lang':  self._q_lang    = val
        else:                  self._q_fmt      = val
        caller.text = label
        for m in (self._quality_menu, self._lang_menu, self._fmt_menu):
            if m: m.dismiss()

    def open_lang_menu(self, caller):
        items = [{'text': n, 'on_release': lambda c=c, n=n: self._set_q(caller, 'lang', c, n)}
                 for c, n in KNOWN_LANGS.items()]
        if self._lang_menu: self._lang_menu.dismiss()
        self._lang_menu = MDDropdownMenu(caller=caller, items=items, width_mult=4)
        self._lang_menu.open()

    def open_fmt_menu(self, caller):
        items = [{'text': f, 'on_release': lambda f=f: self._set_q(caller, 'fmt', f, f)}
                 for f in FORMATS]
        if self._fmt_menu: self._fmt_menu.dismiss()
        self._fmt_menu = MDDropdownMenu(caller=caller, items=items, width_mult=4)
        self._fmt_menu.open()

    def add_item(self):
        url = self.ids.q_url.text.strip()
        if not url:
            self.snack('Enter a URL first.')
            return
        item = {'url': url, 'h': self._q_quality, 'lang': self._q_lang,
                'fmt': self._q_fmt, 'status': 'Pending'}
        self._items.append(item)
        self.ids.q_url.text = ''
        short = url[:36] + '…' if len(url) > 39 else url
        row = TwoLineListItem(
            text=short,
            secondary_text=(f'{RES_LABELS.get(self._q_quality, "1080p")}  ·  '
                            f'{KNOWN_LANGS.get(self._q_lang, self._q_lang)}  ·  '
                            f'{self._q_fmt}  ·  Pending')
        )
        item['_row'] = row
        self.ids.queue_list.add_widget(row)
        self.snack('Added to queue.')

    def clear_done(self):
        remaining = []
        for item in self._items:
            if item['status'] in ('Done', 'Error', 'Skipped'):
                if item.get('_row'):
                    self.ids.queue_list.remove_widget(item['_row'])
            else:
                remaining.append(item)
        self._items = remaining

    def start_queue(self):
        if not [i for i in self._items if i['status'] == 'Pending']:
            self.snack('No pending items.')
            return
        if self._running:
            self.snack('Queue already running.')
            return
        self._running = True
        self._stop    = False
        self.ids.q_start_btn.disabled = True
        outdir = MDApp.get_running_app().settings.get('save_dir', _SAVE_DIR)
        threading.Thread(target=self._worker, args=(outdir,), daemon=True).start()

    def stop_queue(self):
        self._stop = True
        self.snack('Stopping after current item…')

    def _worker(self, outdir):
        total = len([i for i in self._items if i['status'] == 'Pending'])
        done  = 0
        for item in self._items:
            if item['status'] != 'Pending': continue
            if self._stop:
                item['status'] = 'Skipped'
                Clock.schedule_once(lambda dt, i=item: self._set_row(i, 'Skipped'))
                continue
            item['status'] = 'Fetching…'
            Clock.schedule_once(lambda dt, i=item: self._set_row(i, 'Fetching…'))
            try:
                with yt_dlp.YoutubeDL(make_ydl_opts()) as ydl:
                    info = ydl.extract_info(item['url'], download=False)
                fd = parse_info(info)
                h, lang, dl_fmt = item['h'], item['lang'], item['fmt']
                if dl_fmt == 'Audio MP3':   fmt, ac = make_audio_fmt(lang, fd), 'mp3'
                elif dl_fmt == 'Audio WAV': fmt, ac = make_audio_fmt(lang, fd), 'wav'
                else:                       fmt, ac = make_fmt(h, lang, fd), None
                item['status'] = 'Downloading'
                Clock.schedule_once(lambda dt, i=item: self._set_row(i, 'Downloading…'))

                def _hook(d, i=item):
                    if d.get('status') == 'downloading':
                        dl  = d.get('downloaded_bytes', 0) or 0
                        tot = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                        fi, fc = d.get('fragment_index', 0) or 0, d.get('fragment_count', 0) or 0
                        pct = (dl / tot * 100) if tot else ((fi / fc * 100) if fc else 0)
                        Clock.schedule_once(lambda dt, p=pct: (
                            setattr(self.ids.q_prog, 'value', p),
                            setattr(self.ids.q_status, 'text',
                                    f'{p:.0f}%  {fmt_speed(d.get("speed"))}'),
                        ))

                extra = {}
                if ac:
                    pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': ac}
                    if ac == 'mp3': pp['preferredquality'] = '320'
                    extra['postprocessors'] = [pp]
                else:
                    extra['merge_output_format'] = 'mp4'

                with yt_dlp.YoutubeDL(make_ydl_opts(
                    format=fmt,
                    outtmpl=os.path.join(outdir, '%(title)s [%(language)s].%(ext)s'),
                    progress_hooks=[_hook], **extra)) as ydl:
                    ydl.download([item['url']])

                done += 1
                item['status'] = 'Done'
                Clock.schedule_once(lambda dt, i=item: self._set_row(i, '✓ Done'))
                add_to_library({'title': fd.title, 'language': lang,
                                'quality': RES_LABELS.get(h, f'{h}p'),
                                'format': dl_fmt, 'dir': outdir,
                                'date': datetime.now().strftime('%Y-%m-%d %H:%M')})
            except Exception as e:
                item['status'] = 'Error'
                Clock.schedule_once(lambda dt, i=item, er=str(e):
                                    self._set_row(i, f'✗ {er[:30]}'))

            pct_t = done / total * 100 if total else 0
            Clock.schedule_once(lambda dt, p=pct_t, d=done, t=total: (
                setattr(self.ids.q_prog, 'value', p),
                setattr(self.ids.q_status, 'text', f'{d}/{t} done'),
            ))

        self._running = False
        Clock.schedule_once(lambda dt, d=done, t=total: (
            setattr(self.ids.q_start_btn, 'disabled', False),
            setattr(self.ids.q_status, 'text', f'Complete — {d}/{t} downloaded'),
            MDSnackbar(MDLabel(text=f'Queue done! {d}/{t} downloaded.')).open(),
        ))

    def _set_row(self, item, status):
        row = item.get('_row')
        if row:
            row.secondary_text = (
                f'{RES_LABELS.get(item["h"], "1080p")}  ·  '
                f'{KNOWN_LANGS.get(item["lang"], item["lang"])}  ·  '
                f'{item["fmt"]}  ·  {status}')


# ══════════════════════════════════════════════════════════════════════════════
#  Library Tab
# ══════════════════════════════════════════════════════════════════════════════
class LibraryTab(MDBottomNavigationItem):

    def on_tab_touch_up(self, *_):
        self.refresh()

    def refresh(self):
        lst = self.ids.lib_list
        lst.clear_widgets()
        lib = load_library()
        if not lib:
            lst.add_widget(OneLineListItem(text='Nothing downloaded yet.'))
            return
        for entry in lib:
            lst.add_widget(TwoLineListItem(
                text=entry.get('title', 'Unknown')[:46],
                secondary_text=(
                    f'{entry.get("date","—")}  ·  {entry.get("language","—")}  ·  '
                    f'{entry.get("quality","—")}  ·  {entry.get("format","—")}'
                )
            ))

    def clear_history(self):
        try:
            with open(_LIBRARY_F, 'w') as f: json.dump([], f)
        except: pass
        self.refresh()
        MDSnackbar(MDLabel(text='History cleared.')).open()


# ══════════════════════════════════════════════════════════════════════════════
#  Settings Tab
# ══════════════════════════════════════════════════════════════════════════════
class SettingsTab(MDBottomNavigationItem):

    def on_kv_post(self, *_):
        self.ids.save_dir_lbl.text = MDApp.get_running_app().settings.get('save_dir', _SAVE_DIR)

    def open_folder(self):
        d = MDApp.get_running_app().settings.get('save_dir', _SAVE_DIR)
        if not IS_ANDROID and os.path.isdir(d):
            os.startfile(d)
        else:
            MDSnackbar(MDLabel(text=d)).open()


# ══════════════════════════════════════════════════════════════════════════════
#  Main App
# ══════════════════════════════════════════════════════════════════════════════
class MediaGrabApp(MDApp):
    accent_color = ListProperty([0.88, 0.10, 0.08, 1])
    card_color   = ListProperty([0.11, 0.11, 0.12, 1])

    def build(self):
        self.theme_cls.theme_style     = 'Dark'
        self.theme_cls.primary_palette = 'Red'
        self.settings = load_settings()
        self.set_theme(self.settings.get('theme', 'Red'), save=False)

        if IS_ANDROID:
            request_permissions([
                Permission.READ_EXTERNAL_STORAGE,
                Permission.WRITE_EXTERNAL_STORAGE,
            ])

        Builder.load_string(KV)

        nav = MDBottomNavigation(panel_color=[0.09, 0.09, 0.10, 1])

        dl_tab  = DownloadTab(name='download', text='Download', icon='download')
        q_tab   = QueueTab(name='queue',       text='Queue',    icon='playlist-play')
        lib_tab = LibraryTab(name='library',   text='Library',  icon='folder-open')
        set_tab = SettingsTab(name='settings', text='Settings', icon='cog')

        for tab in (dl_tab, q_tab, lib_tab, set_tab):
            nav.add_widget(tab)

        return nav

    def set_theme(self, name, save=True):
        self.accent_color = THEMES.get(name, THEMES['Red'])
        if save:
            self.settings['theme'] = name
            save_settings_file(self.settings)

    def on_stop(self):
        save_settings_file(self.settings)


if __name__ == '__main__':
    MediaGrabApp().run()
