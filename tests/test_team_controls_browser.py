"""Historical time and fictional scheme controls use the same server contracts."""
import pytest
from e2e_server import AppServer
from browser_session import browser
from ui_views import manage, show
from lastmile.exercise_case import FullExercise

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_replay_clock_and_fictional_scheme_controls(browser, tmp_path):
    with AppServer({"LASTMILE_DATA_DIR": str(tmp_path / "server"), "LASTMILE_API_KEY": "controls-key", "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        runner = FullExercise(server.client, "controls-key")
        runner.run()
        ctx = browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server.url + "/teams"); page.wait_for_load_state("networkidle")
        page.get_by_label("Username", exact=True).fill("case-central")
        page.get_by_label("Password", exact=True).fill(runner.password)
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_role("button", name="+ New incident", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Incident name", exact=True).fill("Historical controls exercise")
        dialog.get_by_role("button", name="Open incident", exact=True).click()
        expect(page.get_by_role("heading", name="Historical controls exercise", exact=True)).to_be_visible()
        runner.base = "/api/teams/incidents/" + page.url.split("incident=")[1].split("&")[0]
        manage(page).get_by_role("button", name="Historical replay clock", exact=True).click()
        dialog.get_by_label("As of (ISO time with timezone)", exact=True).fill("2025-11-28T12:00:00+00:00")
        dialog.get_by_label("Reason / receiving note", exact=True).fill("Test original dates in a labelled historical exercise")
        dialog.get_by_role("button", name="Set labelled replay time", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_text("Historical exercise clock: 2025-11-28", exact=False)).to_be_visible()
        source = runner.action("finance", "contribute", {"title": "Fictional policy", "text": "EXERCISE fictional budget 10000.00 LKR, approval limit 8000.00 LKR.", "observed_at": "2025-11-28T12:00:00+00:00"})["sources"][-1]
        runner.action("central", "review_source", {"source_id": source["id"], "decision": "accepted"})
        page.get_by_role("button", name="Sign out", exact=True).click()
        page.get_by_label("Username", exact=True).fill("finance")
        page.get_by_label("Password", exact=True).fill(runner.password)
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
        page.goto(server.url + "/teams#incident=" + runner.base.rsplit("/", 1)[-1])
        expect(page.get_by_role("heading", name="Historical controls exercise", exact=True)).to_be_visible()
        show(page, "Funding records")
        page.get_by_role("button", name="Register funding scheme", exact=True).click()
        dialog.get_by_label("Scheme ID", exact=True).fill("fictional-browser-policy")
        dialog.get_by_label("Scheme name", exact=True).fill("Fictional browser policy")
        dialog.locator("input[name=budget]").fill("10000.00")
        dialog.locator("input[name=limit]").fill("8000.00")
        dialog.locator("input[name=pool_key]").fill("fictional-browser-pool")
        dialog.locator("select[name=source_id]").select_option(source["id"])
        dialog.get_by_role("button", name="Register policy", exact=True).click()
        expect(dialog).not_to_be_visible()
        saved = runner.get("finance")
        assert saved["coordination"]["schemes"][-1]["pool_key"] == "fictional-browser-pool"
        assert saved["coordination"]["replay_at"] == "2025-11-28T12:00:00+00:00"
        assert not errors
        ctx.close(); runner.close_clients()
