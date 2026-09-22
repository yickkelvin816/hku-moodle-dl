"""Per-course crawl logic shared by the check paths: crawl one course,
diff against the previous snapshot, download what's new.

crawl_course     -- serial sync version
acrawl_course    -- async version (runs under a semaphore for parallelism)

Both return a bundle: {course, items, marked, lines, n_saved, n_backfilled},
{course, skipped} for allowlist-skips, or {course, error} is returned by the
caller's wrapper on exception."""
from .config import FILE_TYPES, INCLUDE_CODES
from .crawler import aget_items, get_items
from .downloader import adownload_item, download_item
from .naming import course_code, existing_keys, find_course_dir, item_dest


def _skipped_bundle(course, cname, code):
    return {"course": course, "skipped": True, "lines": [
        f"{cname}: skipped (code {code} not in INCLUDE_CODES)"],
        "items": {}, "marked": {}, "n_saved": 0, "n_backfilled": 0}


def crawl_course(page, ctx, course, prev, prev_time, dry):
    """Crawl ONE course page and download its new items (serial sync path)."""
    cid, cname = course["id"], course["name"]
    code = course_code(cname)
    if INCLUDE_CODES and code not in INCLUDE_CODES:
        return _skipped_bundle(course, cname, code)

    items = get_items(page, cid)

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
    n_saved = n_backfilled = 0

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
        lines = [f"{cname}  ->  {cdir}:"] + lines
    elif prev_time:
        lines = [f"{cname}: no changes"]
    return {"course": course, "items": items, "marked": marked, "lines": lines,
            "n_saved": n_saved, "n_backfilled": n_backfilled}


async def acrawl_course(page, ctx, course, prev, prev_time, dry, sem, existing_registry):
    """async twin of crawl_course + parallelism: runs on its own tab under a
    semaphore; the registry keeps one shared per-course `existing` key-set so
    concurrent courses can't download the same file into two folders."""
    cid, cname = course["id"], course["name"]
    code = course_code(cname)
    if INCLUDE_CODES and code not in INCLUDE_CODES:
        return _skipped_bundle(course, cname, code)

    async with sem:
        items = await aget_items(page, cid)

        old_items = prev.get(cid, {}).get("items", {})
        prev_dl = prev.get(cid, {}).get("downloaded", {})
        new_keys = [k for k in items if k not in old_items]
        renamed = [
            (old_items[k]["title"], items[k]["title"])
            for k in items
            if k in old_items and old_items[k]["title"] != items[k]["title"]
        ]

        cdir, note = find_course_dir(code, cname)
        existing = existing_registry(cid)
        marked = {}
        n_saved = n_backfilled = 0

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
                saved, dups, fails = await adownload_item(
                    ctx, it, item_dest(cdir, it), existing, dry)
                if saved:
                    n_saved += len(saved)
                    extra = "  -> " + ("would save: " if dry else "saved: ") + ", ".join(saved)
                    mark(k, it, saved)
                elif dups and not fails:
                    extra = "  (already on disk)"
                    mark(k, it, [])
            lines.append(f"  + [{it['type']}] {it['title']}  ({it['section']}){extra}")

        # items seen before but never recorded as downloaded: fetch once, then
        # the snapshot remembers them forever (deleting local files will NOT
        # trigger a re-download).
        for k, it in items.items():
            if k in new_keys or it["type"] not in FILE_TYPES or not cdir:
                continue
            if k in prev_dl or k in marked:
                continue
            saved, dups, fails = await adownload_item(
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
            lines = [f"{cname}  ->  {cdir}:"] + lines
        elif prev_time:
            lines = [f"{cname}: no changes"]
        return {"course": course, "items": items, "marked": marked, "lines": lines,
                "n_saved": n_saved, "n_backfilled": n_backfilled}
