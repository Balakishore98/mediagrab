"""
Called by GitHub Actions after buildozer builds the APK.
Uploads bin/*.apk to a Google Drive folder via Service Account.

Required env vars:
  GDRIVE_CREDENTIALS  — full JSON content of the service account key file
  GDRIVE_FOLDER_ID    — Google Drive folder ID (from the folder URL)
"""
import os, json, glob, sys
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

creds_json = os.environ.get('GDRIVE_CREDENTIALS', '')
folder_id  = os.environ.get('GDRIVE_FOLDER_ID', '')

if not creds_json or not folder_id:
    print('ERROR: GDRIVE_CREDENTIALS or GDRIVE_FOLDER_ID secret not set.')
    sys.exit(1)

creds = service_account.Credentials.from_service_account_info(
    json.loads(creds_json),
    scopes=['https://www.googleapis.com/auth/drive.file']
)
service = build('drive', 'v3', credentials=creds)

apks = glob.glob('bin/*.apk')
if not apks:
    print('No APK found in bin/ — build may have failed.')
    sys.exit(1)

for path in apks:
    name = os.path.basename(path)
    meta  = {'name': name, 'parents': [folder_id]}
    media = MediaFileUpload(path, mimetype='application/vnd.android.package-archive',
                            resumable=True)
    f = service.files().create(body=meta, media_body=media,
                               fields='id,name,webViewLink').execute()
    print(f"Uploaded  {f['name']}")
    print(f"View link {f.get('webViewLink', 'n/a')}")
