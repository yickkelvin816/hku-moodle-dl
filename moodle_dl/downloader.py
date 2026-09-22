"""Downloading: fetch resources/folder files, name them, dedupe, save.
Sync twins for the serial path, async twins for the parallel path.

NOTE: in the async API, APIResponse.body() and .text() return COROUTINES —
always await them (a past bug wrote the coroutine object instead of bytes)."""
import html
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config import EXT_BY_TYPE, MAX_FOLDER_FILES
from .naming import norm_key, safe_name


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


async def adownload_one(ctx, url, dest_dir: Path, fallback: str, existing: set, dry: bool):
    """async twin of download_one. Returns (status, filename)."""
    ub = url_basename(url)
    if ub and norm_key(ub) in existing:
        return "dup", ub
    r = await ctx.request.get(url)
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
    p.write_bytes(await r.body())
    return "saved", p.name


async def adownload_item(ctx, item, dest_dir: Path, existing: set, dry: bool):
    """async twin of download_item. Returns (saved, dups, failures)."""
    saved, dups, fails = [], 0, 0
    if item["type"] == "resource":
        st, name = await adownload_one(ctx, item["href"], dest_dir, item["title"], existing, dry)
        if st in ("saved", "would-save"):
            saved.append(name)
        elif st == "dup":
            dups += 1
        else:
            fails += 1
    elif item["type"] == "folder":
        r = await ctx.request.get(item["href"])
        if r.ok:
            links = []
            for link in re.findall(r'href="([^"]*pluginfile\.php[^"]*)"', await r.text()):
                link = html.unescape(link)
                if link not in links:
                    links.append(link)
            for link in links[:MAX_FOLDER_FILES]:
                fb = url_basename(link) or item["title"]
                st, name = await adownload_one(ctx, link, dest_dir, fb, existing, dry)
                if st in ("saved", "would-save"):
                    saved.append(name)
                elif st == "dup":
                    dups += 1
                else:
                    fails += 1
    return saved, dups, fails
