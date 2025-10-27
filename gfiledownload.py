# gdrive_sa_download_and_move.py
# ------------------------------------------------------------
# Service Account ONLY:
#   1) Lists only FILES (no folders) in SOURCE_FOLDER_ID
#   2) Downloads them to Desktop/Kss_output
#      - Non-Google files (incl. .json) => direct download
#      - Google Docs/Sheets/Slides/Drawings/Jamboard => export (PDF/XLSX)
#   3) MOVES the listed items to DEST_FOLDER_ID (replace all parents)
#   4) Robust move() with fallbacks and clear diagnostics
# ------------------------------------------------------------


from __future__ import annotations
import io, os, sys, time, json
from typing import Dict, Optional, Tuple

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
# ======= CONFIG =======
SERVICE_ACCOUNT_FILE = "service_account.json"   # <-- your SA key
SCOPES = ["https://www.googleapis.com/auth/drive"]

SOURCE_FOLDER_ID = "1wIV_AtsIHqL-HGp0v9Rrc1dD6a3UPBiJ"
DEST_FOLDER_ID   = "1gKQp8L_JKtgbFYUU-iLipE_oqQ0ZOfl0"

DEST_PATH = r"C:\KSS_Working\input"
os.makedirs(DEST_PATH, exist_ok=True)

# Debug toggles
DEBUG = True
PRINT_CAPABILITIES_ON_MOVE_ERROR = True

# Export formats for Google file types
EXPORT_MAP: Dict[str, Tuple[str, str]] = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.jam": ("application/pdf", ".pdf"),
    # Forms/Maps/Apps Script: skipped
}
GOOGLE_APPS_PREFIX = "application/vnd.google-apps"
FOLDER_MT   = "application/vnd.google-apps.folder"
SHORTCUT_MT = "application/vnd.google-apps.shortcut"


# ======= HELPERS =======
def build_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False), creds

def retry(callable_factory, *args, retries=5, backoff=1.6, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            return callable_factory(*args, **kwargs).execute()
        except HttpError as e:
            status = getattr(e, "status_code", None) or (e.resp.status if hasattr(e, "resp") else None)
            if status in (429, 500, 502, 503, 504):
                wait = backoff ** attempt
                print(f"⚠️ HTTP {status}. Retry {attempt}/{retries} in {wait:.1f}s...")
                time.sleep(wait)
                continue
            raise
    return callable_factory(*args, **kwargs).execute()

def safe_filename(name: str) -> str:
    for c in '<>:"/\\|?*':
        name = name.replace(c, "_")
    return name.strip()

def ensure_unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{base} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
        i += 1

def get_drive_id(service, folder_id: str) -> Optional[str]:
    meta = retry(service.files().get, fileId=folder_id, fields="id,name,driveId", supportsAllDrives=True)
    return meta.get("driveId")  # None => My Drive

def list_files_in_folder(service, folder_id: str):
    """Yield only FILES (no folders). Includes shortcuts. Handles My Drive & Shared Drives."""
    drive_id = get_drive_id(service, folder_id)
    q = f"'{folder_id}' in parents and trashed=false and mimeType != '{FOLDER_MT}'"
    token = None
    while True:
        kwargs = dict(
            q=q,
            pageSize=1000,
            pageToken=token,
            fields=("nextPageToken,"
                    "files(id,name,mimeType,parents,driveId,"
                    "shortcutDetails(targetId,targetMimeType))"),
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        if drive_id:
            kwargs.update(corpora="drive", driveId=drive_id)
        resp = retry(service.files().list, **kwargs)
        for f in resp.get("files", []):
            yield f
        token = resp.get("nextPageToken")
        if not token:
            break

def resolve_for_download(service, file_obj):
    """Return (download_id, name, mime). Shortcuts download *target* content using shortcut's name."""
    f_id, f_name, f_mt = file_obj["id"], file_obj["name"], file_obj["mimeType"]
    if f_mt == SHORTCUT_MT:
        details = file_obj.get("shortcutDetails") or {}
        tid = details.get("targetId")
        tmt = details.get("targetMimeType")
        if not tid or not tmt:
            meta = retry(service.files().get, fileId=f_id,
                         fields="shortcutDetails(targetId,targetMimeType),name",
                         supportsAllDrives=True)
            details = meta.get("shortcutDetails") or {}
            tid = details.get("targetId")
            tmt = details.get("targetMimeType")
        if not tid or not tmt:
            return None, None, None
        return tid, f_name, tmt
    return f_id, f_name, f_mt

def download_one(service, download_id: str, name: str, mime_type: str, dest_dir: str) -> Optional[str]:
    filename = safe_filename(name)
    if mime_type.startswith(GOOGLE_APPS_PREFIX):
        export = EXPORT_MAP.get(mime_type)
        if not export:
            print(f"🚫 Skipping unsupported Google file: {name} [{mime_type}]")
            return None
        export_mime, ext = export
        path = ensure_unique_path(os.path.join(dest_dir, filename + ext))
        request = service.files().export_media(fileId=download_id, mimeType=export_mime)
    else:
        path = ensure_unique_path(os.path.join(dest_dir, filename))
        request = service.files().get_media(fileId=download_id, supportsAllDrives=True)

    with io.FileIO(path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"⬇️ {name}: {int(status.progress() * 100)}%")
    print(f"✅ Downloaded: {path}")
    return path

def print_capabilities(service, item_id: str):
    try:
        meta = retry(
            service.files().get,
            fileId=item_id,
            fields=("id,name,parents,driveId,owners(emailAddress,displayName),"
                    "capabilities(canEdit,canAddChildren,canMoveItemWithinDrive,"
                    "canMoveItemOutOfDrive,canMoveItemIntoTeamDrive,canMoveItemOutOfTeamDrive)"),
            supportsAllDrives=True,
        )
        print("   🔎 Capabilities:", json.dumps(meta.get("capabilities", {}), indent=2))
        print("   🔎 Parents:", meta.get("parents"))
        print("   🔎 DriveId:", meta.get("driveId"))
        owners = meta.get("owners", [])
        if owners:
            print("   🔎 Owner(s):", ", ".join(o.get("emailAddress","?") for o in owners))
    except HttpError as e:
        print("   (capabilities fetch failed)", e)

def move_one(service, item_id: str, source_folder_id: str, dest_folder_id: str):
    """
    MOVE by replacing all current parents with dest. If parents are hidden,
    retry by removing the known source. All calls set supportsAllDrives=True.
    """
    old_parents = ""
    try:
        meta = retry(service.files().get, fileId=item_id, fields="parents", supportsAllDrives=True)
        prs = meta.get("parents", [])
        old_parents = ",".join(prs) if prs else ""
    except HttpError:
        pass

    # First attempt: remove all visible parents
    try:
        return retry(
            service.files().update,
            fileId=item_id,
            addParents=dest_folder_id,
            removeParents=old_parents,
            fields="id,parents",
            supportsAllDrives=True,
        )
    except HttpError as e1:
        msg = str(e1)
        if DEBUG:
            print(f"   ↩️ First move attempt failed: {msg}")
        # Fallback if Drive hid parents (cannotAddParent due to empty removeParents)
        if ("cannotAddParent" in msg) or ("Increasing the number of parents is not allowed" in msg):
            try:
                return retry(
                    service.files().update,
                    fileId=item_id,
                    addParents=dest_folder_id,
                    removeParents=source_folder_id,
                    fields="id,parents",
                    supportsAllDrives=True,
                )
            except HttpError as e2:
                if DEBUG:
                    print(f"   ↩️ Fallback (remove source only) failed: {e2}")
                raise
        raise


# ======= MAIN =======
if __name__ == "__main__":
    try:
        service, creds = build_service()
        sa_email = getattr(creds, "service_account_email", "(unknown SA)")
        print(f"🔐 Service account: {sa_email}")
        print(f"📂 Source folder:      {SOURCE_FOLDER_ID}")
        print(f"📂 Destination folder: {DEST_FOLDER_ID}")
        print(f"💾 Local save path:    {DEST_PATH}")

        # List only files (no folders)
        items = list(list_files_in_folder(service, SOURCE_FOLDER_ID))
        if not items:
            print("⚠️ No files in source (or no access).")
            sys.exit(0)

        # Download
        print("\n=== Downloading files ===")
        to_move = []
        for it in items:
            dl_id, dl_name, dl_mime = resolve_for_download(service, it)
            if not dl_id:
                print(f"🚫 Skipping unresolved item: {it.get('name','(no name)')}")
                continue
            try:
                if download_one(service, dl_id, dl_name, dl_mime, DEST_PATH):
                    to_move.append((it["id"], it["name"]))
            except HttpError as e:
                print(f"❌ Failed to download {dl_name}: {e}")

        if not to_move:
            print("\n⚠️ Nothing downloaded; aborting move.")
            sys.exit(1)

        # Move
        print("\n=== Moving items in Drive (source ➜ destination) ===")
        for item_id, item_name in to_move:
            try:
                move_one(service, item_id, SOURCE_FOLDER_ID, DEST_FOLDER_ID)
                print(f"🚚 Moved: {item_name}")
            except HttpError as e:
                print(f"❌ Failed to move {item_name}: {e}")
                if PRINT_CAPABILITIES_ON_MOVE_ERROR:
                    print_capabilities(service, item_id)
                print("   👉 Fix tips: Share BOTH folders with the service account email as "
                      "'Editor' (My Drive) or 'Content manager' (Shared drive). "
                      "Enable 'Editors can organize, add & edit' in folder share settings.")

        print("\n🎉 Done.")

    except HttpError as e:
        print("❌ Google API error:", e)
    except Exception as ex:
        print("❌ Unexpected error:", ex)
