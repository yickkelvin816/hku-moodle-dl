"""Central configuration: paths, allowlist, limits, MIME map.

Everything the user might want to tweak lives here.
"""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PROFILE = BASE / "profile"
STATE = BASE / "state.json"           # cookie snapshot incl. session cookies
SNAPSHOT = BASE / "snapshot.json"
MOODLE = "https://moodle.hku.hk"
DOWNLOADS_ROOT = Path.home() / "Downloads" / "HKU Moodle Download"
# Only crawl courses whose code appears in this set. EMPTY SET = crawl EVERY course.
INCLUDE_CODES = {"CAES9542", "COMP3323", "COMP3353", "COMP3355", "COMP4801"}
FILE_TYPES = {"resource", "folder"}
MAX_FOLDER_FILES = 60
# concurrent course-tab crawlers in `check` (network-bound: 4 is plenty)
MAX_WORKERS = 4

EXT_BY_TYPE = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "video/mp4": ".mp4",
}
