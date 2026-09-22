#!/usr/bin/env python3
"""HKU Moodle watcher - report new materials and mirror files under ~/Downloads. No AI involved.

Setup (once):
    python3 -m pip install --user playwright
    # Google Chrome must be installed (it is on this machine)

Usage:
    python3 hku_moodle.py login            # opens Chrome; YOU sign in via HKU Portal SSO + MFA
                                           # (click "Yes" on Microsoft's "Stay signed in?")
    python3 hku_moodle.py courses          # list Moodle courses and their download folder mapping
    python3 hku_moodle.py check            # crawl, report new items, download new files
    python3 hku_moodle.py check --dry-run  # show what would be downloaded, write nothing
    python3 hku_moodle.py check --serial   # one-course-at-a-time crawl (parallel is the default)
    python3 hku_moodle.py check --headed   # debug only: show the (otherwise headless) browser
    python3 hku_moodle.py status           # show the last snapshot

How it works:
- The signed-in session lives in ./profile (a dedicated Chromium profile folder) and
  survives reboots. Redo `login` only when Microsoft SSO eventually expires.
- `check` crawls /my/courses.php and every course page (in parallel: one tab per
  course) and mirrors files into ~/Downloads/HKU Moodle Download/<COURSE>/<Section>/...
  following the Moodle course page structure: each activity lands in its section's
  folder, and "folder" activities get their own subfolder named after the activity.
  Course folders are named <CODE>_<Name> (e.g. COMP3353_Bioinformatics).
- snapshot.json records every item that has ever been downloaded (or was already on
  disk when first seen). That RECORD drives the download decision, not the filesystem:
  once an item is recorded, deleting its local files does NOT make `check` download it
  again. To force a re-download, remove the item from snapshot.json's "downloaded" map
  (or delete snapshot.json entirely for a full re-mirror).
- Only courses whose code is in INCLUDE_CODES are crawled (empty set = every course).

Code layout (see moodle_dl/):
    config.py    paths, allowlist, limits    |  crawler.py    dashboard/course-page scraping
    naming.py    folder/file naming, dedupe  |  downloader.py fetching + saving files
    session.py   browser/SSO/cookies/login   |  course_run.py per-course crawl+download
    checker.py   check/courses/status commands
"""
import sys

from moodle_dl.checker import do_check, do_courses, do_status
from moodle_dl.session import do_login


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "login":
        sys.exit(do_login())
    elif cmd == "check":
        sys.exit(do_check(
            dry="--dry-run" in sys.argv,
            headless="--headed" not in sys.argv,      # headless unless asked
            parallel="--serial" not in sys.argv,      # parallel unless asked
        ))
    elif cmd == "courses":
        sys.exit(do_courses())
    elif cmd == "status":
        sys.exit(do_status())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
