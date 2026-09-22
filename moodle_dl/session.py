"""Browser session: launching the dedicated Chromium profile, cookie
persistence, HKU Portal SSO helpers, and the interactive `login` flow."""
import asyncio
import json
import time

from .config import MOODLE, PROFILE, STATE


def logged_in(url: str) -> bool:
    return "login/index.php" not in url


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


async def alaunch(pw, headless):
    ctx = await pw.chromium.launch_persistent_context(str(PROFILE), headless=headless)
    if STATE.exists():
        try:
            data = json.loads(STATE.read_text())
            cookies = data if isinstance(data, list) else data.get("cookies", [])
            if cookies:
                await ctx.add_cookies(cookies)
        except Exception:
            pass
    return ctx


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


async def a_try_silent_sso(page) -> bool:
    try:
        await page.goto(f"{MOODLE}/login/index.php?authCAS=CAS", wait_until="load")
    except Exception:
        return False
    for _ in range(15):
        await asyncio.sleep(1.0)
        if page.url.startswith(MOODLE):
            return logged_in(page.url)
    return False


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
