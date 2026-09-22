"""Pure naming/mapping logic: course codes, safe folder/file names,
course->folder mapping, and normalized dedupe keys. No I/O except
scanning the download tree."""
import re
import unicodedata
from pathlib import Path

from .config import DOWNLOADS_ROOT


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
