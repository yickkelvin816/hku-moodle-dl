# hku-moodle-dl

Watches your HKU Moodle courses, reports new materials since the last check, and
mirrors course files to `~/Downloads/HKU Moodle Download/` — recreating each
course page's folder structure on disk.

Use only Python + Playwright without browser extension or keylogging.

## Features

- Reports new items per course since the last `check`
- Mirrors Moodle's structure: `<course>/<section>/...`, folder activities nested deeper
- No re-downloads: `snapshot.json` records every item ever fetched — deleting
  files locally never triggers a re-download
- Session persists across reboots; sign in once, re-auth every few weeks
- Course allowlist (`INCLUDE_CODES`) to crawl only what you care about

## Requirements

- Python 3.9+
- An HKU account (Moodle via HKU Portal SSO + MFA)
- macOS / Linux / Windows

## Installation

```bash
git clone <repo-url> && cd hku-moodle-dl
python3 -m pip install --user playwright
python3 -m playwright install chromium
```

Uses Playwright's own Chromium — your everyday Chrome/Safari is never touched.

## Usage

### First sign-in

```bash
python3 hku_moodle.py login
```

A Chromium window opens at HKU Portal. Sign in yourself, approve MFA, click
**Yes** on "Stay signed in?", then wait — the script completes the redirect
chain and prints `Login OK`.

### Daily

```bash
python3 hku_moodle.py check            # crawl, report new items, download new files
python3 hku_moodle.py check --dry-run  # preview only, write nothing
python3 hku_moodle.py status           # show last snapshot
python3 hku_moodle.py courses          # list courses + folder mapping
```

Downloads land as:

```
~/Downloads/HKU Moodle Download/
└── MOOD1234_CourseName/
    ├── Lecture_notes/
    │   ├── Lec01_-_Course_overview.pdf
    │   └── ...
    └── Assignments/
        └── ...
```

The first `check` builds a baseline and lists everything — that's normal.

### Re-downloading on purpose

The snapshot decides, not the disk. To force a re-download, remove the item's
entry from `snapshot.json`'s `"downloaded"` map — or delete `snapshot.json`
entirely for a full re-mirror. (A file *replaced* under an already-downloaded
activity is not re-fetched.)

## Configuration

Edit the constants at the top of `hku_moodle.py`:

| Constant | Default | Meaning |
|---|---|---|
| `DOWNLOADS_ROOT` | `~/Downloads/HKU Moodle Download` | Mirror root. |
| `INCLUDE_CODES` | several COMP/CAES codes | Course codes to crawl. `set()` = every course. |

## Troubleshooting

| Problem | Fix |
|---|---|
| `Not logged in (session expired?)` | Run `login` again. |
| Chromium launch error right after another run | Profile lock — wait a few seconds or `pkill -f hku_moodle`, retry. |
| Login window parked on the portal page | Wait; the script nudges up to 3 times. |
| Crawl died midway (rare) | Re-run `check`; it's idempotent. |

## Security

`state.json` and `profile/` contain live login cookies — **never share them**.
Share only `hku_moodle.py`. Everything runs locally and talks only to
`moodle.hku.hk`, `hkuportal.hku.hk`, and `login.microsoftonline.com`.
