"""Crawling: reading the course dashboard and course pages.
Sync twins for the serial path, async twins for the parallel path —
kept line-for-line equivalent."""
import asyncio
import re
import time

from .session import a_try_silent_sso, logged_in, try_silent_sso
from .config import MOODLE


def get_courses(page):
    page.goto(f"{MOODLE}/my/courses.php", wait_until="domcontentloaded")
    time.sleep(1.5)
    if not logged_in(page.url):
        if not try_silent_sso(page):
            return None
        page.goto(f"{MOODLE}/my/courses.php", wait_until="domcontentloaded")
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
    page.goto(f"{MOODLE}/course/view.php?id={course_id}", wait_until="domcontentloaded")
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


# ------------------------------------------------- async twins (parallel) ----
# The sync Playwright API is thread-pinned (greenlets), so concurrency needs
# the async API. These twins mirror get_courses/get_items exactly; anything
# pure (course_code, safe_name, norm_key, ...) is shared with the sync path.

async def aget_courses(page):
    await page.goto(f"{MOODLE}/my/courses.php", wait_until="domcontentloaded")
    await asyncio.sleep(1.5)
    if not logged_in(page.url):
        if not await a_try_silent_sso(page):
            return None
        await page.goto(f"{MOODLE}/my/courses.php", wait_until="domcontentloaded")
        await asyncio.sleep(1.5)
        if not logged_in(page.url):
            return None
    raw = await page.eval_on_selector_all(
        'a[href*="/course/view.php?id="]',
        "els => els.map(e => ({href: e.href, name: (e.getAttribute('aria-label') || e.innerText || '').trim()}))",
    )
    courses, seen = [], set()
    for c in raw:
        m = re.search(r"id=(\d+)", c["href"])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        lines = [
            ln.strip()
            for ln in c["name"].split("\n")
            if ln.strip() and ln.strip().lower() != "course name"
        ]
        name = " ".join(lines) or f"course-{m.group(1)}"
        courses.append({"id": m.group(1), "name": name})
    return courses


async def aget_items(page, course_id):
    await page.goto(f"{MOODLE}/course/view.php?id={course_id}", wait_until="domcontentloaded")
    await asyncio.sleep(1.5)
    raw = await page.evaluate("""() => {
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
    }""")
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
        section = re.sub(r"^(Collapse|Expand)\s+", "", section)
        section = re.sub(r"\s+(Collapse|Expand) all$", "", section)
        if not section:
            section = f"Section {it.get('num') or 1}"
        items[key] = {
            "type": typ, "id": iid, "title": title,
            "href": it["href"], "section": section,
        }
    return items
