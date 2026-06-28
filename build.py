"""
Build MediaGrab.exe using PyInstaller.
Run:  python build.py
"""

import subprocess
import sys
import os
import shutil

ROOT      = os.path.dirname(os.path.abspath(__file__))
DIST_DIR  = os.path.join(ROOT, 'dist')
BUILD_DIR = os.path.join(ROOT, '_build')

# Ensure PyInstaller is available
try:
    import PyInstaller
except ImportError:
    print('Installing PyInstaller…')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])

cmd = [
    sys.executable, '-m', 'PyInstaller',
    '--onefile',
    '--noconsole',
    '--name', 'MediaGrab',
    '--distpath', DIST_DIR,
    '--workpath', BUILD_DIR,
    '--specpath', ROOT,

    # Bundle packages
    '--collect-all', 'yt_dlp',
    '--collect-all', 'customtkinter',

    # Extra hidden imports
    '--hidden-import', 'yt_dlp.extractor.lazy_extractors',
    '--hidden-import', 'PIL._tkinter_finder',

    # Windows exe metadata
    '--version-file', os.path.join(ROOT, 'version_info.txt'),

    os.path.join(ROOT, 'app.py'),
]

# Generate version_info.txt
version_info = '''
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName',      u'MediaGrab'),
         StringStruct(u'FileDescription',  u'MediaGrab - Video Downloader'),
         StringStruct(u'FileVersion',      u'1.0.0'),
         StringStruct(u'InternalName',     u'MediaGrab'),
         StringStruct(u'LegalCopyright',   u''),
         StringStruct(u'OriginalFilename', u'MediaGrab.exe'),
         StringStruct(u'ProductName',      u'MediaGrab'),
         StringStruct(u'ProductVersion',   u'1.0.0')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
'''.strip()

with open(os.path.join(ROOT, 'version_info.txt'), 'w') as f:
    f.write(version_info)

print('Building MediaGrab.exe …')
result = subprocess.run(cmd, cwd=ROOT)

if result.returncode == 0:
    exe = os.path.join(DIST_DIR, 'MediaGrab.exe')
    print(f'\nBuild successful: {exe}')
else:
    print('\nBuild failed - see output above.')
    sys.exit(1)
