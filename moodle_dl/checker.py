"""Command orchestration: `check` (parallel + serial), `courses`, `status`.
Snapshot-robustness rules (both paths):
- a course that errors carries its previous snapshot entry forward;
- if EVERY course errors, exit 4 and leave snapshot.json byte-identical."""
import asyncio
import json
from datetime import datetime

from .config import (
    DOWNLOADS_ROOT,
    FILE_TYPES,
    INCLUDE_CODES,
    MAX_WORKERS,
    SNAPSHOT,
    STATE,
)
from .course_run import acrawl_course, crawl_course
from .crawler import aget_courses, get_courses, get_items
from .naming import course_code, existing_keys, find_course_dir
from .session import alaunch, launch, do_login  # noqa: F401  (re-export)


def load_prev():
    prev, prev_time = {}, None
    if SNAPSHOT.exists():
        old = json.loads(SNAPSHOT.read_text())
        prev_time = old.get("checked_at")
        prev = old.get("courses", {})
    return prev, prev_time


def collect(results, prev, state, report):
    """Fold per-course bundles into report lines + new snapshot state.

    Returns (fresh, n_saved, n_backfilled): fresh = courses crawled this run.
    """
    fresh = n_saved = n_backfilled = 0
    for res in results:
        if res.get("error"):
            report.append(
                f"{res['course']['name']}: ERROR crawling ({res['error']})")
            # keep the previous snapshot entry so one bad crawl never
            # erases download history (and thus never re-downloads
            # everything on the next run)
            cid = res["course"]["id"]
            if cid in prev:
                state["courses"][cid] = prev[cid]
            continue
        report.extend(res["lines"])
        if res.get("skipped"):
            continue
        fresh += 1
        state["courses"][res["course"]["id"]] = {
            "name": res["course"]["name"],
            "items": res["items"],
            "downloaded": res["marked"],
        }
        n_saved += res["n_saved"]
        n_backfilled += res["n_backfilled"]
    return fresh, n_saved, n_backfilled


def finish(state, report, prev_time, n_saved, n_backfilled, dry: bool) -> int:
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


# --------------------------------------------------------------- check ----
async def ado_check(dry: bool, headless: bool) -> int:
    from playwright.async_api import async_playwright

    prev, prev_time = load_prev()

    async with async_playwright() as pw:
        ctx = await alaunch(pw, headless=headless)
        page = await ctx.new_page()
        page.set_default_timeout(45000)
        courses = await aget_courses(page)
        if courses is None:
            print("Not logged in (session expired?). Run:  python3 hku_moodle.py login")
            await ctx.close()
            return 3
        cookies = await ctx.cookies()
        STATE.write_text(json.dumps(cookies, indent=1))  # keep cookies fresh
        STATE.chmod(0o600)
        await page.close()          # dashboard tab no longer needed

        state = {"checked_at": datetime.now().isoformat(timespec="seconds"), "courses": {}}
        report = []

        if not courses:
            print("No courses found on /my/courses.php -- check selectors/theme.")

        # one tab per course, at most MAX_WORKERS crawling simultaneously
        sem = asyncio.Semaphore(MAX_WORKERS)
        existing_sets = {}          # course id -> disk key-set (created once)

        def existing_registry(cid):
            if cid not in existing_sets:
                course = next(c for c in courses if c["id"] == cid)
                cdir, _ = find_course_dir(course_code(course["name"]), course["name"])
                existing_sets[cid] = existing_keys(cdir)
            return existing_sets[cid]

        async def run_one(course):
            page = await ctx.new_page()
            page.set_default_timeout(45000)
            try:
                return await acrawl_course(
                    page, ctx, course, prev, prev_time, dry, sem, existing_registry)
            except Exception as e:
                return {"course": course, "error": str(e)}
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

        results = await asyncio.gather(*(run_one(c) for c in courses))

        fresh, n_saved, n_backfilled = collect(results, prev, state, report)

        # nothing crawled successfully -> do not touch the snapshot at all
        if fresh == 0 and any(r.get("error") for r in results):
            print("ERROR: every course failed to crawl -- keeping the old "
                  "snapshot untouched.")
            await ctx.close()
            return 4

        await ctx.close()

    return finish(state, report, prev_time, n_saved, n_backfilled, dry)


def do_check(dry: bool, headless: bool, parallel: bool) -> int:
    if parallel:
        return asyncio.run(ado_check(dry, headless))
    from playwright.sync_api import sync_playwright

    prev, prev_time = load_prev()

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
        report = []

        if not courses:
            print("No courses found on /my/courses.php -- check selectors/theme.")
        results = []
        for c in courses:
            try:
                results.append(crawl_course(page, ctx, c, prev, prev_time, dry))
            except Exception as e:
                results.append({"course": c, "error": str(e)})

        fresh, n_saved, n_backfilled = collect(results, prev, state, report)

        # nothing crawled successfully -> do not touch the snapshot at all
        if fresh == 0 and any(r.get("error") for r in results):
            print("ERROR: every course failed to crawl -- keeping the old "
                  "snapshot untouched.")
            ctx.close()
            return 4

        ctx.close()

    return finish(state, report, prev_time, n_saved, n_backfilled, dry)


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
