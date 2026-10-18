"""Coordination tools in a real browser: priority and escalation, stock on record, reading bulletins,
map layers, who does what where, language and partner access."""
import json
import re

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter
from test_requests_browser import switch

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect

# Fictional figures in the published layouts; not real readings.
BULLETIN = """EXERCISE copy of a DMC situation report as at 1000hrs on 09th October 2026
20 Nuwara Eliya 2100 6900 1800 6100 2 1 5 41 4 120 400 3 90 300 10 40 100 340
EXERCISE NBRO Level 3 (Red) landslide early warning for Kothmale and Walapane DS divisions.
"""


def test_requests_bulletins_layers_3w_language_and_partner(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "tools"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "division", "divisional_secretary")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        incident = re.search(r"incident=([a-z0-9_]+)", page.url).group(1)
        modal = page.get_by_role("dialog")
        # A request with priority and a registered delivery place; the form shows what NWSDB holds on record.
        page.get_by_role("button", name="New request", exact=False).first.click()
        modal.get_by_label("What kind of help?", exact=True).select_option("water")
        expect(modal.locator(".stock-hint")).to_contain_text(re.compile(r"Water bowser: \d+ bowsers ready \(6 at NWSDB Nuwara Eliya regional yard, in this district\)"))
        modal.get_by_label("What exactly is needed?", exact=True).fill("Drinking water for the hall")
        modal.get_by_label("Priority", exact=True).select_option("urgent")
        modal.get_by_label("Registered centre (optional)", exact=True).select_option("centre-kotmale-practice-hall")
        expect(modal.get_by_label("Delivery place (optional)", exact=True)).to_have_value("Kotmale Practice Hall")
        modal.get_by_role("button", name="Send request", exact=True).click()
        expect(modal).not_to_be_visible()
        card = page.get_by_role("article", name="Drinking water for the hall", exact=True)
        expect(card).to_contain_text("Urgent")
        expect(card).to_contain_text("Deliver to Kotmale Practice Hall, Kothmale DS, Nuwara Eliya")
        card.get_by_role("button", name="Escalate", exact=True).click()
        modal.get_by_label("Reason", exact=True).fill("Forty people have no water")
        modal.get_by_role("button", name="Escalate", exact=True).click()
        expect(card.locator(".escalation-note")).to_contain_text("Escalated to District (DDMCU)")
        # The district coordinator it reached sends it to NDRSC.
        switch(page, "district", "district_coordinator")
        card.get_by_role("button", name="Send to another body", exact=True).click()
        modal.get_by_label("Send to", exact=True).select_option("ndrsc")
        modal.get_by_label("Reason", exact=True).fill("NDRSC holds bottled water")
        modal.get_by_role("button", name="Send to another body", exact=True).click()
        expect(card.locator(".ask-route")).to_contain_text("NDRSC")
        # An official bulletin in a report becomes figures and a warning after a person ticks them.
        switch(page, "dmc", "duty_officer")
        page.get_by_role("button", name="Add report", exact=False).first.click()
        modal.get_by_label("Title", exact=False).first.fill("Exercise situation report")
        modal.locator("textarea").first.fill(BULLETIN)
        modal.get_by_role("button", name=re.compile("^(Add report|Save|Upload|Share)")).last.click()
        expect(modal).not_to_be_visible()
        page.get_by_role("tab", name=re.compile("^Reports")).click()
        report = page.get_by_role("article", name="Exercise situation report", exact=True)
        report.get_by_role("button", name="Read figures & warnings", exact=True).click()
        expect(modal).to_contain_text("Nuwara Eliya: 6,100 people affected")
        expect(modal.get_by_label("Kothmale DS, Nuwara Eliya", exact=True)).to_be_checked()
        # A bulletin with no separate advice starts from its own warning wording, quoted exactly.
        expect(modal.get_by_label("Advice from the bulletin", exact=True)).to_have_value("EXERCISE NBRO Level 3 (Red) landslide early warning for Kothmale and Walapane DS divisions.")
        modal.get_by_label("Advice from the bulletin", exact=True).fill("Exercise: move to a safety centre if instructed.")
        modal.get_by_role("button", name="Add ticked records", exact=True).click()
        expect(modal).not_to_be_visible()
        # Reading the same report again: what is already recorded starts unticked.
        report.get_by_role("button", name="Read figures & warnings", exact=True).click()
        expect(modal.get_by_label("Kothmale DS, Nuwara Eliya", exact=True)).not_to_be_checked()
        expect(modal.locator("input[name=sitrep]").first).not_to_be_checked()
        modal.get_by_role("button", name="Cancel", exact=True).click()
        page.get_by_role("tab", name="Map & status", exact=True).click()
        expect(page.get_by_role("heading", name="Official warnings in force (", exact=False)).to_contain_text("Official warnings in force")
        expect(page.locator(".warning-list")).to_contain_text("Exercise: move to a safety centre if instructed.")
        # A flood extent someone else produced, drawn on the map.
        layer = tmp_path / "extent.geojson"
        layer.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "Exercise extent"},
                                     "geometry": {"type": "Polygon", "coordinates": [[[80.58, 7.01], [80.60, 7.01], [80.60, 7.03], [80.58, 7.01]]]}}]}))
        page.get_by_role("button", name="+ Map layer", exact=True).click()
        modal.get_by_label("GeoJSON file", exact=True).set_input_files(str(layer))
        expect(modal).to_contain_text("1 feature read.")
        modal.get_by_label("Produced by", exact=True).fill("Copernicus EMS (exercise copy)")
        modal.get_by_role("button", name="Add layer", exact=True).click()
        expect(modal).not_to_be_visible()
        expect(page.get_by_role("region", name="Map layers (1)", exact=True)).to_contain_text("Copernicus EMS (exercise copy)")
        # Who does what where lists the accepted work and service restoration.
        page.goto(server.url + "/#view=whowhat")
        expect(page.get_by_role("heading", name="Who does what where", level=1, exact=True)).to_be_visible()
        expect(page.get_by_role("region", name="Kothmale DS, Nuwara Eliya", exact=True)).to_contain_text("RDA")
        page.get_by_role("group", name="Group by", exact=True).get_by_role("button", name="Matrix", exact=True).click()
        expect(page.locator("table.w3-matrix")).to_contain_text("Roads & access")
        # Sinhala interface labels from Workspace settings; records stay as written.
        page.locator("#workspace-settings").click()
        page.locator("#staff-language").select_option("si")
        page.get_by_role("dialog").locator("button[type=submit]").click()
        expect(page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="ඉල්ලීම්", exact=True)).to_be_visible()
        expect(page.locator("html")).to_have_attribute("lang", "si")
        page.locator("#workspace-settings").click()
        page.locator("#staff-language").select_option("en")
        page.get_by_role("dialog").locator("button[type=submit]").click()
        expect(page.locator("html")).to_have_attribute("lang", "en")
        # A partner sees the incident it was invited to, but cannot open incidents or see funding.
        switch(page, "partner", "partner_coordinator")
        expect(page.get_by_role("banner")).to_contain_text("limited access")
        page.goto(server.url + "/")
        expect(page.get_by_role("button", name="+ New incident", exact=True)).to_have_count(0)
        page.goto(server.url + f"/#incident={incident}&tab=requests")
        page.wait_for_load_state("networkidle")  # the board redraws once when Places & stock arrives
        page.get_by_role("button", name="More", exact=True).click()
        expect(page.get_by_role("menuitem", name="Funding records", exact=True)).to_have_count(0)
        assert not errors, errors
        context.close()
