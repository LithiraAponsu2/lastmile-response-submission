"""The public asks for help on the status page; staff see it in the incident and the Dashboard queue; the tracker follows."""
import struct
import zlib

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter
from ui_views import show
from lastmile.team_areas import DIVISIONS

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
KOTHMALE = DIVISIONS["ds-nuwara-eliya-kothmale"]["centre"]
GALLE = next(v["centre"] for k, v in DIVISIONS.items() if k.startswith("ds-galle-"))


def png(path):
    """A real 2 × 2 PNG, so the browser can read and shrink it."""
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x80\x00" * 2 for _ in range(2))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def ask(page, url, where, text, photo=None):
    """A person on the public page: Ask for help, describe, add a photo, use their location, send."""
    page.context.set_geolocation({"latitude": where[0], "longitude": where[1]})
    page.goto(url + "/public")
    page.get_by_role("button", name="Ask for help", exact=True).click()
    dialog = page.get_by_role("dialog", name="Ask for help or report a problem")
    dialog.get_by_label("What is happening? Any language.", exact=True).fill(text)
    if photo:
        dialog.get_by_label("Photos (up to 3, optional)", exact=True).set_input_files(str(photo))
    dialog.locator("#ask-locate").click()
    expect(dialog.locator("#ask-place")).to_contain_text("Your location")
    dialog.get_by_role("button", name="Send request", exact=True).click()
    done = page.locator("#ask-done")
    expect(done).to_contain_text("Request sent", timeout=20000)
    expect(done.locator(".track-item")).to_contain_text("Being reviewed")
    code = done.locator(".code").inner_text()
    done.get_by_role("button", name="Close", exact=True).click()
    return code


def test_public_request_to_incident_card_and_tracker(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "public"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        staff = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce").new_page()
        errors = []
        staff.on("pageerror", lambda e: errors.append(str(e)))
        enter(staff, server.url + "/", "dmc", "duty_officer")
        expect(staff.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        citizen = browser.new_context(viewport={"width": 390, "height": 844}, geolocation={"latitude": KOTHMALE[0], "longitude": KOTHMALE[1]}, permissions=["geolocation"]).new_page()
        citizen.on("pageerror", lambda e: errors.append(str(e)))
        code = ask(citizen, server.url, KOTHMALE, "Our electricity is broken since the storm last night, 12 people in our lane have no power.", png(tmp_path / "line.png"))
        citizen.screenshot(path=str(tmp_path / "public-page.png"), full_page=True)
        # Staff: the request is in the incident, read, with a card for the power provider.
        staff.reload()
        # Public requests have their own tab; the Overview points to it.
        expect(staff.get_by_role("button", name="Open Public requests", exact=False)).to_be_visible()
        staff.get_by_role("tab", name="Public requests (1)", exact=True).click()
        section = staff.locator("#public-requests")
        expect(section).to_contain_text("Electricity · Kothmale DS, Nuwara Eliya")
        expect(section).to_contain_text("Our electricity is broken since the storm")
        expect(section).to_contain_text("The person sees: Being reviewed")
        # A small card: the suggested request in one line, the rest under Details.
        request = section.get_by_role("article", name="Electricity · Kothmale DS, Nuwara Eliya", exact=True)
        expect(request).to_contain_text("Suggested: Electricity for Kothmale DS")
        expect(request.locator(".public-photos img")).to_have_count(0)
        request.locator(".public-more > summary").click()
        expect(request.locator(".public-photos img")).to_have_count(1)
        expect(request.locator("div.plan-card")).to_contain_text("Ask Electricity Distribution Lanka (CEB) to handle it")
        # The suggestion sits with the request it answers, and nowhere else.
        expect(staff.locator("#ai-suggestions div.plan-card").filter(has_text="Electricity for Kothmale DS")).to_have_count(0)
        staff.screenshot(path=str(tmp_path / "staff-public.png"), full_page=True)
        request.get_by_role("button", name="Send suggested request", exact=True).click()
        expect(staff.locator("#toast")).to_have_text("Request sent")
        expect(section).to_contain_text("The person sees: Being handled by")
        expect(section.get_by_role("button", name="Open request", exact=False)).to_be_visible()
        # The person sees who is handling it, with no personal details.
        citizen.goto(server.url + "/public?track=1")  # the tracker lists requests sent from this device
        expect(citizen.get_by_role("dialog", name="Track a request")).to_be_visible()
        expect(citizen.locator("#my-requests")).to_contain_text("Being handled by Electricity Distribution Lanka (CEB)")
        expect(citizen.locator("#my-requests")).to_contain_text(code)
        # A request where no incident is open waits on the Dashboard for a coordinator.
        ask(citizen, server.url, GALLE, "Flood water entered our houses, 40 people need dry food and drinking water.")
        staff.goto(server.url + "/")
        staff.get_by_role("heading", name="Dashboard", level=1, exact=True).wait_for()
        queue = staff.locator("#dash-public")
        expect(queue).to_contain_text("Public requests waiting")
        expect(queue).to_contain_text("No open incident covers")
        expect(queue.get_by_role("button", name="Open new incident", exact=True)).to_be_visible()
        staff.screenshot(path=str(tmp_path / "dashboard-queue.png"), full_page=True)
        queue.get_by_role("button", name="Open new incident", exact=True).click()
        modal = staff.get_by_role("dialog")
        expect(modal.get_by_label("Title", exact=True)).to_have_value("Food · Galle")
        modal.get_by_role("button", name="Open incident", exact=True).click()
        expect(staff.get_by_role("heading", name="Food · Galle", level=1, exact=True)).to_be_visible(timeout=20000)
        staff.get_by_role("tab", name="Public requests (1)", exact=True).click()
        expect(staff.locator("#public-requests")).to_contain_text("Flood water entered our houses")
        # The sidebar page lists every public request, with the incident and what the person sees.
        staff.goto(server.url + "/#view=inbox")
        expect(staff.get_by_role("heading", name="Public requests", level=1, exact=True)).to_be_visible()
        filed = staff.locator("#public-filed")
        expect(filed.get_by_role("article")).to_have_count(2)
        expect(filed).to_contain_text("Being handled by")
        expect(filed).to_contain_text("Food · Galle")
        staff.get_by_label("Search public requests").fill("electricity")
        expect(filed.get_by_role("article").filter(visible=True)).to_have_count(1)
        filed.get_by_role("article").filter(visible=True).get_by_role("button", name="Open in incident", exact=False).click()
        expect(staff.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        expect(staff.get_by_role("tab", name="Public requests (1)", exact=True)).to_have_attribute("aria-selected", "true")
        assert errors == []


def test_a_district_is_never_narrowed_to_one_division(browser, tmp_path):
    """Regression: choosing a district quietly selected its first DS division, in the area search and the help form."""
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "district"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        page = browser.new_context(viewport={"width": 320, "height": 700}, reduced_motion="reduce").new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server.url + "/public")
        page.locator("#totals .total").first.wait_for()  # areas and the picture have loaded
        search = page.locator("#area-search")
        search.fill("Mannar")
        results = page.locator("#area-results")
        results.get_by_role("button", name="Mannar District").click()
        expect(results).to_contain_text("Choose your DS division:")
        expect(results.get_by_role("button", name="Madhu DS · Mannar")).to_be_visible()
        expect(page.locator("#area")).to_be_hidden()  # nothing is shown for a division the person did not choose
        results.get_by_role("button", name="Madhu DS · Mannar").click()
        expect(page.get_by_role("heading", name="Madhu DS, Mannar", level=2)).to_be_visible()
        page.get_by_role("button", name="Ask for help", exact=True).click()
        dialog = page.get_by_role("dialog", name="Ask for help or report a problem")
        dialog.locator("#ask-area").fill("Mannar")
        dialog.locator("#ask-area-results").get_by_role("button", name="Mannar District").click()
        expect(dialog.locator("#ask-place")).to_have_text("Mannar District")
        assert page.evaluate("document.documentElement.scrollWidth") <= 320  # no sideways scrolling on a small phone
        assert errors == []


def test_a_possible_repeat_waits_for_the_coordinator(browser, tmp_path):
    """Two reports of the same need in one division: the second is flagged, and the coordinator keeps them separate."""
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "repeat"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        staff = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce").new_page()
        errors = []
        staff.on("pageerror", lambda e: errors.append(str(e)))
        enter(staff, server.url + "/", "dmc", "duty_officer")
        expect(staff.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        citizen = browser.new_context(viewport={"width": 390, "height": 844}, geolocation={"latitude": KOTHMALE[0], "longitude": KOTHMALE[1]}, permissions=["geolocation"]).new_page()
        ask(citizen, server.url, KOTHMALE, "20 people have no electricity at Temple A after the storm.")
        ask(citizen, server.url, KOTHMALE, "60 people have no electricity at School B after the storm.")
        staff.reload()
        staff.get_by_role("tab", name="Overview", exact=True).wait_for()
        expect(staff.locator(".attention-list")).to_contain_text("public request to check")
        staff.get_by_role("tab", name="Public requests (2)", exact=True).click()
        section = staff.locator("#public-requests")
        expect(section).to_contain_text("Possibly the same problem as")
        sends = section.get_by_role("button", name="Send suggested request", exact=True)
        expect(sends).to_have_count(1)
        section.get_by_role("button", name="Different place or need", exact=True).click()
        expect(staff.locator("#toast")).to_contain_text("Kept separate")
        expect(sends).to_have_count(2)
        expect(section).not_to_contain_text("Possibly the same problem as")
        # Without a suggestion, a coordinator writes the request from the person's words.
        first = section.get_by_role("article").filter(has=staff.get_by_role("button", name="Send suggested request", exact=True)).first
        first.locator(".public-more > summary").click()
        first.get_by_role("button", name="Dismiss", exact=True).click()
        expect(sends).to_have_count(1)
        section.get_by_role("button", name="Create request", exact=False).click()
        modal = staff.get_by_role("dialog")
        expect(modal.get_by_label("What exactly is needed?", exact=True)).to_have_value("Electricity for 60 people, Kothmale DS, Nuwara Eliya")
        expect(modal.get_by_label("Note (optional)", exact=True)).to_contain_text("Public request LM-")
        modal.get_by_role("button", name="Send request", exact=True).click()
        expect(modal).not_to_be_visible()
        staff.get_by_role("tab", name="Public requests (2)", exact=True).click()
        expect(section.get_by_role("button", name="Open request", exact=False)).to_have_count(1)
        staff.screenshot(path=str(tmp_path / "repeat.png"), full_page=True)
        assert errors == []
