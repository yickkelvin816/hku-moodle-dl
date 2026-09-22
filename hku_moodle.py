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
    python3 hku_moodle.py check --headed   # same, with a visible browser window
    python3 hku_moodle.py status           # show the last snapshot

How it works:
- The signed-in session lives in ./profile (a dedicated Chromium profile folder) and
  survives reboots. Redo `login` only when Microsoft SSO eventually expires.
- `check` crawls /my/courses.php and every course page and mirrors files into
  ~/Downloads/HKU Moodle Download/<COURSE>/<Section>/... following the Moodle course
  page structure: each activity lands in its section's folder, and "folder" activities
  get their own subfolder named after the activity. Course folders are named
  <CODE>_<Name> (e.g. COMP3353_Bioinformatics).
- snapshot.json records every item that has ever been downloaded (or was already on
  disk when first seen). That RECORD drives the download decision, not the filesystem:
  once an item is recorded, deleting its local files does NOT make `check` download it
  again. To force a re-download, remove the item from snapshot.json's "downloaded" map
  (or delete snapshot.json entirely for a full re-mirror).
- Only courses whose code is in INCLUDE_CODES are crawled (empty set = every course).
"""
import html
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE = Path(__file__).resolve().parent
PROFILE = BASE / "profile"
STATE = BASE / "state.json"           # cookie snapshot incl. session cookies
SNAPSHOT = BASE / "snapshot.json"
MOODLE = "https://moodle.hku.hk"
DOWNLOADS_ROOT = Path.home() / "Downloads" / "HKU Moodle Download"
# Only crawl courses whose code appears in this set. EMPTY SET = crawl EVERY course.
INCLUDE_CODES = {"CAES9542", "COMP3323", "COMP3353", "COMP3355", "COMP4801"}
FILE_TYPES = {"resource", "folder"}
MAX_FOLDER_FILES = 60

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


def launch(pw, headless):
    """Always use Playwright's bundled Chromium — a SEPARATE browser from the user's
    Google Chrome app, so running checks never disturbs their normal browsing.
    Cookies from state.json (saved at login, INCLUDING session cookies, which
    Chromium otherwise drops on close) are re-injected on every launch."""
    ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=headless)
    if STATE.exists():
        try:
            import json as _json

            data = _json.loads(STATE.read_text())
            cookies = data if isinstance(data, list) else data.get("cookies", [])
            if cookies:
                ctx.add_cookies(cookies)
        except Exception:
            pass
    return ctx


def logged_in(url: str) -> bool:
    return "login/index.php" not in url


# ---------------------------------------------------------------- login ----
def do_login() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = launch(pw, headless=False)
        page = ctx.new_page()
        page.goto(f"{MOODLE}/login/index.php?authCAS=CAS", wait_until="load")
        print("Chrome window opened -- please sign in with your HKU account (SSO + MFA).")
        print('Click "Yes" on Microsoft\'s "Stay signed in?" so the session lasts longer.')
        print("Waiting for the Moodle dashboard (up to 30 minutes)...")
        deadline = time.time() + 1800
        last, since = "", time.time()
        nudges = 0
        while time.time() < deadline:
            done = False
            for p in ctx.pages:
                if p.url.startswith(MOODLE) and logged_in(p.url):
                    time.sleep(3)              # let Chromium flush cookies to disk
                    try:
                        cookies = ctx.cookies()
                        STATE.write_text(json.dumps(cookies, indent=1))
                        STATE.chmod(0o600)
                        print(f"Login OK -- {len(cookies)} cookies saved to {STATE.name}.")
                    except Exception as e:
                        print("Cookie save failed:", str(e)[:100])
                    p.goto(f"{MOODLE}/my/courses.php", wait_until="load")
                    done = True
                    break
            if done:
                ctx.close()
                return 0
            url = next((p.url for p in ctx.pages if p.url != "about:blank"), "")
            if url != last:
                print(f"  at: {url[:110]}", flush=True)
                last, since = url, time.time()
            # Nudge: page parked on the portal form while an AAD session may exist
            if (
                url.startswith("https://hkuportal.hku.hk/cas/")
                and time.time() - since > 45
                and nudges < 3
            ):
                nudges += 1
                print(f"  nudge {nudges}: re-requesting CAS ticket...", flush=True)
                try:
                    ctx.pages[0].goto(
                        f"{MOODLE}/login/index.php?authCAS=CAS", wait_until="load"
                    )
                except Exception as e:
                    print("  nudge failed:", str(e)[:80])
                since = time.time()
            time.sleep(2)
        print("Timed out waiting for login; nothing saved.")
        ctx.close()
        return 2


# --------------------------------------------------------------- mapping ----
def course_code(name: str):
    """Extract e.g. COMP3353 from a course name like 'COMP3353 Bioinformatics'."""
    m = re.search(r"([A-Z]{2,4}\d{4})", re.sub(r"\s+", "", name.upper()))
    return m.group(1) if m else None


def safe_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\w\s.\-]", "", s).strip()
    return re.sub(r"[\s/]+", "_", s)[:90] or "untitled"


def course_folder_name(code, name: str) -> str:
    """<CODE>_<Name> folder name, e.g. COMP3353_Bioinformatics."""
    n = re.sub(r"\(\s*20\d{2}\s*[-\u2013]\s*20\d{2}[^)]*\)", "", name)   # drop "(2026-2027 ...)"
    n = re.sub(r"\((?:Semester|Sem|Trimester|Term)[^)]*\)", "", n, flags=re.I)
    n = re.sub(r"\[[^\]]*\]", "", n)                                     # drop "[2026]", "[Section 1A, 2026]"
    if code:
        rest = re.sub(re.escape(code), "", n, count=1).strip(" -_:,")
        n = f"{code}_{rest}" if rest else code
    n = safe_name(n)[:60]
    return re.sub(r"[._\- ]+$", "", n) or (code or "Unknown_Course")


def find_course_dir(code, name: str):
    """Map a course to its folder under DOWNLOADS_ROOT.

    Folders already present are matched by course-code prefix (so a teacher
    renaming the course does not fork the folder). Returns (path, note); path may
    be a not-yet-existing folder for new courses.
    """
    if not code:
        return DOWNLOADS_ROOT / course_folder_name(None, name), "no course code in name"
    cand = sorted(
        d
        for d in (DOWNLOADS_ROOT.iterdir() if DOWNLOADS_ROOT.is_dir() else [])
        if d.is_dir() and d.name.upper().replace(" ", "").startswith(code)
    )
    exact = [d for d in cand if re.match(rf"^{code}(_|$)", d.name.upper().replace(" ", ""))]
    if exact:
        return exact[0], ""
    if cand:
        return cand[0], "fuzzy match"
    return DOWNLOADS_ROOT / course_folder_name(code, name), "new folder"


def item_dest(course_dir: Path, item) -> Path:
    """Mirror the Moodle course-page layout:
    <course>/<section>/<file>          for single resources
    <course>/<section>/<folder title>/ for folder activities
    """
    sec = safe_name(item.get("section") or "General")
    if item["type"] == "folder":
        return course_dir / sec / safe_name(item["title"])
    return course_dir / sec


def norm_key(filename: str) -> str:
    """Normalized filename key: lowercase stem without punctuation + extension."""
    p = Path(filename)
    stem = re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", p.stem).lower())
    return stem + p.suffix.lower()


def existing_keys(course_dir) -> set:
    if course_dir and course_dir.is_dir():
        return {norm_key(p.name) for p in course_dir.rglob("*") if p.is_file()}
    return set()


# ---------------------------------------------------------------- crawl ----
def try_silent_sso(page) -> bool:
    """Re-establish a Moodle session via CAS if the AAD/Portal token is still valid."""
    try:
        page.goto(f"{MOODLE}/login/index.php?authCAS=CAS", wait_until="load")
    except Exception:
        return False
    for _ in range(15):                      # let the redirect chain settle
        time.sleep(1.0)
        if page.url.startswith(MOODLE):
            return logged_in(page.url)
    return False


def get_courses(page):
    page.goto(f"{MOODLE}/my/courses.php", wait_until="load")
    time.sleep(1.5)
    if not logged_in(page.url):
        if not try_silent_sso(page):
            return None
        page.goto(f"{MOODLE}/my/courses.php", wait_until="load")
        time.sleep(1.5)
        if not logged_in(page.url):
            return None
    raw = page.eval_on_selector_all(
        'a[href*="/course/view.php?id="]',
        "els => els.map(e => ({href: e.href, name: (e.getAttribute('aria-label') || e.innerText || '').trim()}))",
    )
    courses, seen = [], set()
    for c in raw:
        m = re.search(r"id=(\d+)", c["href"])
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue
        seen.add(cid)
        lines = [
            ln.strip()
            for ln in c["name"].split("\n")
            if ln.strip() and ln.strip().lower() != "course name"
        ]
        name = " ".join(lines) or f"course-{cid}"
        courses.append({"id": cid, "name": name})
    return courses


def get_items(page, course_id):
    page.goto(f"{MOODLE}/course/view.php?id={course_id}", wait_until="load")
    time.sleep(1.5)
    raw = page.evaluate(
        """() => {
            const secs = Array.from(
                document.querySelectorAll('li.section, li.course-section, section'));
            const out = [];
            document.querySelectorAll('li.activity, .activity-item').forEach(act => {
                const a = act.querySelector('a.aalink, .activity-instance a');
                if (!a) return;
                let section = '', num = 0;
                const sec = act.closest('li.section, li.course-section, section');
                if (sec) {
                    num = secs.indexOf(sec) + 1;
                    const h = sec.querySelector(
                        '.sectionname, .course-section-header, .section-title, h3');
                    section = h ? (h.innerText || h.getAttribute('aria-label') || '') : '';
                }
                out.push({href: a.href, title: (a.innerText || '').trim(),
                          section: section.trim(), num});
            });
            return out;
        }"""
    )
    items, seen = {}, set()
    for it in raw:
        m = re.search(r"/mod/(\w+)/view\.php\?id=(\d+)", it["href"])
        if not m:
            continue
        typ, iid = m.group(1), m.group(2)
        key = f"{typ}/{iid}"
        if key in seen:
            continue
        seen.add(key)
        title = it["title"].split("\n")[0].strip() or f"{typ}-{iid}"
        section = " ".join(it.get("section", "").split())
        # strip the collapse/expand toggle text Moodle puts in the section header
        section = re.sub(r"^(Collapse|Expand)\s+", "", section)
        section = re.sub(r"\s+(Collapse|Expand) all$", "", section)
        if not section:
            section = f"Section {it.get('num') or 1}"
        items[key] = {
            "type": typ, "id": iid, "title": title,
            "href": it["href"], "section": section,
        }
    return items


# ------------------------------------------------------------- download ----
def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suffix, n = p.stem, p.suffix, 2
    while True:
        cand = p.with_name(f"{stem}-{n}{suffix}")
        if not cand.exists():
            return cand
        n += 1


def url_basename(u: str) -> str:
    return Path(unquote(urlparse(u).path)).name


def filename_from(resp, url, fallback):
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="([^"]+)"', cd)
    if m:
        return unquote(m.group(1))
    base = url_basename(url)
    if base and "." in base:
        return base
    ct = (resp.headers.get("content-type") or "").split(";")[0].strip()
    return fallback + EXT_BY_TYPE.get(ct, "")


def download_one(ctx, url, dest_dir: Path, fallback: str, existing: set, dry: bool):
    """Fetch url; decide save/skip. Returns (status, filename)."""
    ub = url_basename(url)
    if ub and norm_key(ub) in existing:          # fast path: URL already tells the name
        return "dup", ub
    r = ctx.request.get(url)
    if not r.ok:
        return "fail", ""
    ct = (r.headers.get("content-type") or "").split(";")[0].strip()
    if "text/html" in ct:
        return "html", ""
    name = filename_from(r, r.url, fallback)
    if norm_key(name) in existing:
        return "dup", name
    existing.add(norm_key(name))
    if dry:
        return "would-save", name
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = unique_path(dest_dir / safe_name(name))
    p.write_bytes(r.body())
    return "saved", p.name


def download_item(ctx, item, dest_dir: Path, existing: set, dry: bool):
    """Download a resource or folder activity. Returns (saved, dups, failures)."""
    saved, dups, fails = [], 0, 0
    if item["type"] == "resource":
        st, name = download_one(ctx, item["href"], dest_dir, item["title"], existing, dry)
        if st in ("saved", "would-save"):
            saved.append(name)
        elif st == "dup":
            dups += 1
        else:
            fails += 1
    elif item["type"] == "folder":
        r = ctx.request.get(item["href"])
        if r.ok:
            links = []
            for link in re.findall(r'href="([^"]*pluginfile\.php[^"]*)"', r.text()):
                link = html.unescape(link)
                if link not in links:
                    links.append(link)
            for link in links[:MAX_FOLDER_FILES]:
                fb = url_basename(link) or item["title"]
                st, name = download_one(ctx, link, dest_dir, fb, existing, dry)
                if st in ("saved", "would-save"):
                    saved.append(name)
                elif st == "dup":
                    dups += 1
                else:
                    fails += 1
    return saved, dups, fails


# ---------------------------------------------------------------- check ----
def do_check(dry: bool, headless: bool) -> int:
    from playwright.sync_api import sync_playwright

    prev = {}
    prev_time = None
    if SNAPSHOT.exists():
        old = json.loads(SNAPSHOT.read_text())
        prev_time = old.get("checked_at")
        prev = old.get("courses", {})

    with sync_playwright() as pw:
        ctx = launch(pw, headless=headless)
        page = ctx.new_page()
        page.set_default_timeout(45000)
        courses = get_courses(page)
        if courses is None:
            print("Not logged in (session expired?). Run:  python3 hku_moodle.py login")
            ctx.close()
            return 3
        STATE.write_text(json.dumps(ctx.cookies(), indent=1))  # keep cookies fresh
        STATE.chmod(0o600)

        state = {"checked_at": datetime.now().isoformat(timespec="seconds"), "courses": {}}
        report, n_saved, n_backfilled = [], 0, 0

        if not courses:
            print("No courses found on /my/courses.php -- check selectors/theme.")
        for c in courses:
            cid, cname = c["id"], c["name"]
            code = course_code(cname)
            if INCLUDE_CODES and code not in INCLUDE_CODES:
                report.append(f"{cname}: skipped (code {code} not in INCLUDE_CODES)")
                continue
            try:
                items = get_items(page, cid)
            except Exception as e:
                report.append(f"{cname}: ERROR crawling ({e})")
                continue

            old_items = prev.get(cid, {}).get("items", {})
            prev_dl = prev.get(cid, {}).get("downloaded", {})
            new_keys = [k for k in items if k not in old_items]
            renamed = [
                (old_items[k]["title"], items[k]["title"])
                for k in items
                if k in old_items and old_items[k]["title"] != items[k]["title"]
            ]

            cdir, note = find_course_dir(code, cname)
            existing = existing_keys(cdir)
            marked = {}       # download marks decided during THIS run

            def mark(k, it, files):
                dest = item_dest(cdir, it)
                marked[k] = {"files": files, "dest": str(dest.relative_to(cdir))}

            lines = []
            if note:
                lines.append(f"  (folder: {note})")

            for k in new_keys:
                it = items[k]
                extra = ""
                if it["type"] in FILE_TYPES and cdir:
                    saved, dups, fails = download_item(
                        ctx, it, item_dest(cdir, it), existing, dry)
                    if saved:
                        n_saved += len(saved)
                        extra = "  -> " + ("would save: " if dry else "saved: ") + ", ".join(saved)
                        mark(k, it, saved)
                    elif dups and not fails:
                        extra = "  (already on disk)"
                        mark(k, it, [])
                lines.append(f"  + [{it['type']}] {it['title']}  ({it['section']}){extra}")

            # items seen before but never recorded as downloaded (first run after
            # the switch to ~/Downloads, or earlier failures): fetch once, then
            # the snapshot remembers them forever -- deleting local files will
            # NOT trigger a re-download.
            for k, it in items.items():
                if k in new_keys or it["type"] not in FILE_TYPES or not cdir:
                    continue
                if k in prev_dl or k in marked:
                    continue
                saved, dups, fails = download_item(
                    ctx, it, item_dest(cdir, it), existing, dry)
                if saved:
                    n_backfilled += len(saved)
                    lines.append(f"  \u21b7 downloaded: {', '.join(saved)}")
                    mark(k, it, saved)
                elif dups and not fails:
                    mark(k, it, [])

            # carry forward marks for items that need no attention this run
            for k, rec in prev_dl.items():
                if k in items and k not in marked:
                    marked[k] = rec

            for old_t, new_t in renamed:
                lines.append(f"  ~ renamed: {old_t}  =>  {new_t}")

            if lines:
                report.append(f"{cname}  ->  {cdir}:\n" + "\n".join(lines))
            elif prev_time:
                report.append(f"{cname}: no changes")
            state["courses"][cid] = {"name": cname, "items": items, "downloaded": marked}

        ctx.close()

    if not dry:
        SNAPSHOT.write_text(json.dumps(state, indent=2, ensure_ascii=False))

    tag = "[dry] " if dry else ""
    if not prev_time:
        total = sum(len(c["items"]) for c in state["courses"].values())
        print(f"{tag}Baseline saved: {len(state['courses'])} courses, {total} items.")
    else:
        print(f"{tag}=== New materials since {prev_time} ===")
    print("\n".join(report) if report else "Nothing.")
    if n_saved or n_backfilled or dry:
        verb = "would download" if dry else "downloaded"
        print(f"\n{tag}{n_saved + n_backfilled} file(s) {verb} into '{DOWNLOADS_ROOT}'.")
    return 0


# --------------------------------------------------------------- courses ----
def do_courses() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = launch(pw, headless=True)
        page = ctx.new_page()
        page.set_default_timeout(45000)
        courses = get_courses(page)
        ctx.close()
    if courses is None:
        print("Not logged in (session expired?). Run:  python3 hku_moodle.py login")
        return 3
    print(f"{len(courses)} course(s) on /my/courses.php:")
    for c in courses:
        code = course_code(c["name"])
        if INCLUDE_CODES and code not in INCLUDE_CODES:
            print(f"  [off] {c['name']}  (id {c['id']}) -- code {code} not in INCLUDE_CODES")
            continue
        cdir, note = find_course_dir(code, c["name"])
        print(f"  {c['name']}  (id {c['id']})\n      -> {cdir}{'  [' + note + ']' if note else ''}")
    return 0


# ---------------------------------------------------------------- status ----
def do_status() -> int:
    if not SNAPSHOT.exists():
        print(f"No snapshot yet at {SNAPSHOT} -- run a `check` first.")
        return 1
    s = json.loads(SNAPSHOT.read_text())
    print(f"Last check: {s['checked_at']}   ({SNAPSHOT})")
    for cid, c in s["courses"].items():
        types = {}
        for it in c["items"].values():
            types[it["type"]] = types.get(it["type"], 0) + 1
        detail = ", ".join(f"{v} {k}" for k, v in sorted(types.items()))
        ndl = len(c.get("downloaded", {}))
        print(f"  {c['name']}: {len(c['items'])} items ({detail}); {ndl} recorded as downloaded")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "login":
        sys.exit(do_login())
    elif cmd == "check":
        sys.exit(do_check(dry="--dry-run" in sys.argv, headless="--headed" not in sys.argv))
    elif cmd == "courses":
        sys.exit(do_courses())
    elif cmd == "status":
        sys.exit(do_status())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
