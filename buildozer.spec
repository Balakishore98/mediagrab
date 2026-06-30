[app]
title           = MediaGrab
package.name    = mediagrab
package.domain  = com.mediagrab

source.dir      = .
source.include_exts = py,png,jpg,kv,atlas
source.include_patterns = app_mobile.py

version         = 3.0
requirements    = python3,kivy==2.3.1,kivymd==1.2.0,yt-dlp,pillow,certifi,charset-normalizer,idna,urllib3,requests

orientation     = portrait
fullscreen      = 0
android.minapi  = 21
android.api     = 34
android.ndk     = 25b

android.permissions = INTERNET, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE

android.entrypoint = app_mobile:MediaGrabApp

[buildozer]
log_level = 2
warn_on_root = 1
