"""The public warning map in a real browser (Chromium via Playwright), against the real server process.

Optional: skipped unless Playwright is installed (pip install -r requirements-e2e.txt; playwright install chromium).
Set LASTMILE_E2E_SCREENSHOTS=<dir> to save a screenshot of each step (useful for the README and the deck).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from clock import live_text
from e2e_server import AppServer
from lastmile.eval import load_corpus

playwright_sync = pytest.importorskip("playwright.sync_api")
expect = playwright_sync.expect

CORPUS = {c["id"]: c for c in load_corpus()}
SHOTS = os.environ.get("LASTMILE_E2E_SCREENSHOTS")
FALLBACK_CHROMIUM = ("/opt/pw-browsers/chromium",)  # preinstalled in Claude Code cloud sessions


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as first:  # noqa: BLE001 — a Playwright build without its own browser download
            exe = os.environ.get("LASTMILE_E2E_CHROMIUM") or next((x for x in FALLBACK_CHROMIUM if Path(x).exists()), None)
            if not exe:
                pytest.skip(f"no Chromium for Playwright: {first}")
            b = p.chromium.launch(executable_path=exe)
        yield b
        b.close()


class PageWatch:
    """Collects browser errors and dialogs so a test can assert none ran; saves optional screenshots."""

    def __init__(self, page) -> None:
        self.page, self.errors, self.dialogs = page, [], []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: (self.dialogs.append(d.message), d.dismiss()))

    def shot(self, name: str) -> None:
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def _wait_map_loaded(page) -> None:
    """The map <img> has loaded and decoded. Polled with expect(), not wait_for_function("..."): the pages' CSP
    (script-src 'self') forbids the string evaluation that wait_for_function needs once it has to poll."""
    expect(page.locator("#map")).to_have_js_property("complete", True)
    expect(page.locator("#map")).not_to_have_js_property("naturalWidth", 0)


# ------------------------------------------------------------------ the public danger map
@pytest.fixture(scope="module")
def public_server():
    with AppServer({"LASTMILE_RATE_LIMIT_PER_MIN": "1000", "LASTMILE_PUBLIC_RATE_LIMIT_PER_MIN": "1000",
                    "LASTMILE_DEV_MAILBOX": "1"}) as s:
        yield s


def _mailbox(server):
    import httpx
    return httpx.get(server.url + "/api/dev/mailbox").json()


def _publish(server, case_id):
    import httpx
    rid = httpx.post(server.url + "/api/runs", json={"pack_id": "lk-landslide", "source_text": CORPUS[case_id]["text"]}).json()["id"]
    return httpx.post(server.url + f"/api/runs/{rid}/review", json={"reviewer": "A. Perera", "decision": "approve"}).json()


def test_public_map_subscribe_confirm_and_get_the_email(browser, public_server):
    ctx = browser.new_context(viewport={"width": 1366, "height": 1000})
    page = ctx.new_page()
    con = PageWatch(page)

    page.goto(public_server.url + "/alerts")
    expect(page.locator("#alerts")).to_contain_text("No warnings are in force")
    # subscribe in Sinhala for Passara, drills included
    page.fill("#email", "nimali@example.com")
    page.check("#areaChecks input[value='LK-BD-PAS']")
    page.select_option("#subLang", "si")
    page.check("#drills")
    page.click("#subscribe")
    expect(page.locator("#toast")).to_contain_text("tick the consent box")      # consent is not optional
    page.check("#consent")
    page.click("#subscribe")
    expect(page.locator("#toast")).to_contain_text("confirmation link")
    confirm = re.search(r"(/alerts\?confirm=[\w-]+)", _mailbox(public_server)[0]["body"]).group(1)
    page.goto(public_server.url + confirm)
    expect(page.locator("#notice")).to_contain_text("Subscription confirmed")

    done = _publish(public_server, "lk-estates-cyclone-season")
    mail = _mailbox(public_server)[0]
    assert mail["to"] == "nimali@example.com" and mail["subject"].startswith("[පුහුණුව]")
    assert "- පස්සර: 3 වන මට්ටම – රතු." in mail["body"]

    # the map as of the alert's time, in Sinhala, with area lookup and reference check
    page.goto(public_server.url + "/alerts?at=2025-11-27T08:00:00%2B05:30")
    page.click("#langs .lang:has-text('සිංහල')")
    expect(page.locator("#title")).to_contain_text("අනතුරු සිතියම")
    expect(page.locator("#areaChecks")).to_contain_text("පස්සර")              # the form follows the language too
    expect(page.locator("#exerciseBanner")).to_be_visible()
    expect(page.locator("#alerts li").first).to_contain_text("පස්සර")
    page.select_option("#area", "LK-NE-WAL")
    expect(page.locator("#areaResult")).to_contain_text("3 වන මට්ටම – රතු")
    page.fill("#ref", done["outbox"]["reference"])
    page.click("#checkRef")
    expect(page.locator("#refResult")).to_contain_text("Genuine")
    page.fill("#ref", "FAKE01")
    page.click("#checkRef")
    expect(page.locator("#refResult")).to_contain_text("not among the warnings in force")
    _wait_map_loaded(page)
    con.shot("12-public-map-si")

    # sign in by email link, change preferences, then delete
    page.locator("#signinBox summary").click()
    page.fill("#signinEmail", "nimali@example.com")
    page.click("#signin")
    expect(page.locator("#toast")).to_contain_text("sign-in link")
    signin = re.search(r"(/alerts\?signin=[\w-]+)", _mailbox(public_server)[0]["body"]).group(1)
    page.goto(public_server.url + signin)
    expect(page.locator("#manageWho")).to_contain_text("nimali@example.com")
    page.check("#areaChecks input[value='LK-KY']")
    page.click("#save")
    expect(page.locator("#toast")).to_contain_text("Saved")
    page.click("#deleteMe")
    expect(page.locator("#toast")).to_contain_text("deleted")
    expect(page.locator("#subscribe")).to_be_visible()
    assert con.dialogs == [] and [e for e in con.errors if "Content Security Policy" in e] == []
    ctx.close()


def test_public_map_fits_a_phone(browser, public_server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, device_scale_factor=2)
    page = ctx.new_page()
    page.goto(public_server.url + "/alerts?at=2025-11-27T08:00:00%2B05:30")
    expect(page.locator("#title")).to_contain_text("Danger map")
    _wait_map_loaded(page)
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if SHOTS:
        page.screenshot(path=str(Path(SHOTS) / "13-public-phone.png"), full_page=True)
    ctx.close()
    assert overflow <= 1, f"public page is {overflow}px wider than a phone"
