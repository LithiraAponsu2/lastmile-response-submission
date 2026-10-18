"""Audit fictional local team screens with a supplied official axe-core script.

Uses a temporary local server and offline model. No cloud/model calls or dispatch.
Install requirements-e2e.txt first. An automated pass is not WCAG certification.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from e2e_server import AppServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axe-script", type=Path, required=True, help="Local official axe.min.js; it is never added to the application")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for results and screenshots")
    parser.add_argument("--chromium", default=os.environ.get("LASTMILE_E2E_CHROMIUM"))
    args = parser.parse_args()
    if not args.axe_script.is_file():
        parser.error("Provide an existing official axe-core script")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    from playwright.sync_api import sync_playwright
    checks = []
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **({"executable_path": args.chromium} if args.chromium else {}))
        # Allow QA-only axe injection without weakening the application's CSP.
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce", bypass_csp=True)
        page = context.new_page()
        def audit(name):
            page.wait_for_load_state("networkidle")
            page.add_script_tag(path=str(args.axe_script.resolve()))
            result = page.evaluate("""async () => {
              const result = await axe.run(document, {
                runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice']},
                resultTypes: ['violations','incomplete']
              });
              return {version: result.testEngine.version, violations: result.violations, incomplete: result.incomplete};
            }""")
            result["screen"] = name
            checks.append(result)
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path=str(args.output_dir / (name + ".png")), full_page=True)
            print(f"{name}: {len(result['violations'])} violations; {len(result['incomplete'])} manual-review rules", flush=True)
        page.goto(server.url + "/")
        audit("home-before-role")
        page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="Dashboard", exact=True).click()
        audit("incident-list")
        page.get_by_role("button", name="Try sample case", exact=True).click()
        audit("entry-role-dialog")
        page.locator("input[name=organisation][value=water]").check()
        audit("role-picker-water")
        page.locator("input[name=organisation][value=dmc]").check()
        page.locator("input[name=role][value=duty_officer]").check()
        page.get_by_role("button", name="Enter workspace", exact=True).click()
        page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True).wait_for()
        audit("overview-desktop")
        page.get_by_label("Ask or describe the next step", exact=True).fill("What needs attention?")
        page.get_by_role("button", name="Send message", exact=True).click()
        activity = page.locator("lm-work-activity")
        activity.get_by_text("Assistant finished", exact=True).wait_for(timeout=30000)
        audit("activity-collapsed")
        activity.get_by_text("Assistant finished", exact=True).click()
        audit("activity-expanded")
        # A plan card from the flood request (after its one clarifying question), its edit dialog, and the issued letter.
        composer = page.get_by_label("Ask or describe the next step", exact=True)
        composer.fill("Kothmale and Walapane at Red, Hanguranketa Amber, river at major flood. Send a report to NDRSC and the Divisional Secretariat asking dry rations for 1,200 families, and notify them.")
        page.get_by_role("button", name="Send message", exact=True).click()
        page.locator("#chat-log .bubble.assistant").filter(has_text="own assessment").wait_for(timeout=30000)
        composer.fill("It is our own assessment.")
        page.get_by_role("button", name="Send message", exact=True).click()
        card = page.locator("div.plan-card")
        card.wait_for(timeout=30000)
        audit("plan-card")
        card.get_by_role("button", name="Edit step 3", exact=True).click()
        audit("plan-edit-dialog")
        page.keyboard.press("Escape")
        card.get_by_role("button", name="Apply all steps", exact=True).click()
        page.locator("#toast").filter(has_text="All steps applied").wait_for()
        page.locator(".more-views > .more-trigger").click()
        page.locator(".more-views").get_by_role("menuitem", name="Documents", exact=False).click()
        page.locator(".letter-record").first.get_by_role("button", name="Open letter", exact=True).click()
        audit("letter-view")
        page.keyboard.press("Escape")
        page.locator(".more-views > .more-trigger").click()
        audit("more-views-menu")
        page.keyboard.press("Escape")
        page.locator(".more-views > .more-trigger").click()
        page.get_by_role("button", name="New request", exact=True).click()
        audit("new-request-dialog")
        page.keyboard.press("Escape")
        for label in ["Map & status", "Requests", "Reports", "Shelters & resources", "Needs & assignments", "Funding records", "Documents", "Shared with you", "Activity"]:
            tab = page.get_by_role("tab", name=label, exact=False)
            if not tab.count():
                if not page.locator(".more-views").evaluate("el => el.open"):
                    page.locator(".more-views > .more-trigger").click()
                page.locator(".more-views").get_by_role("menuitem", name=label, exact=False).click()
                tab = page.get_by_role("tab", name=label, exact=False)
            tab.click()
            audit(label.lower().replace(" & ", "-").replace(" ", "-"))
        flow = page.locator('lm-incident-flow')
        flow.get_by_role('button', name='Fit workflow').wait_for()
        flow.locator('button.node.request').first.hover()
        page.wait_for_function("document.querySelector('lm-incident-flow').shadowRoot.querySelector('wa-tooltip[open]') !== null")
        audit('workflow-point-tooltip')
        page.keyboard.press('Escape')
        page.get_by_label('Workflow focus', exact=True).select_option(index=1)
        flow.get_by_role('button', name='Fit workflow').wait_for()
        audit('workflow-focused')
        page.get_by_role('button', name='Summarise progression', exact=True).click()
        page.get_by_label('AI progression summary').locator('summary').click(timeout=30000)
        audit('workflow-ai-summary')
        page.get_by_role("button", name="Upload a report", exact=True).click()
        audit("upload-dialog")
        page.get_by_role("dialog").get_by_text("Source timing · change for an older report", exact=True).click()
        audit("upload-dialog-expanded")
        page.keyboard.press("Escape")
        page.locator("#switch-role").click()
        audit("role-dialog")
        page.set_viewport_size({"width": 390, "height": 844})
        audit("mobile-role-dialog")
        page.keyboard.press("Escape")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("tab", name="Overview", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        audit("mobile-chat")
        page.get_by_role("button", name="Live data", exact=True).click()
        audit("mobile-data")
        page.set_viewport_size({"width": 320, "height": 700})
        audit("narrow-data")
        page.locator('.more-views > .more-trigger').click()
        page.locator('.more-views').get_by_role('menuitem', name='Activity', exact=True).click()
        page.locator('lm-incident-flow').get_by_role('button', name='Fit workflow').wait_for()
        audit('workflow-mobile')
        page.set_viewport_size({"width": 1440, "height": 1000})
        for key, label in [("requests", "Requests"), ("activity", "Activity"), ("sources", "Reports"), ("resources", "Places & stock"), ("map", "Status map"), ("whowhat", "Who does what where"), ("tasks", "Needs & assignments"), ("communications", "Communications")]:
            page.goto(server.url + "/#view=" + key)
            page.get_by_role("heading", name=label, exact=True).wait_for()
            if key == "requests":
                page.get_by_label("Search requests", exact=True).wait_for()
            elif key == "activity":
                page.get_by_label("Search activity", exact=True).wait_for()
            elif key == "whowhat":
                page.get_by_role("group", name="Group by", exact=True).wait_for()
            elif key == "resources":
                page.get_by_label("Search Safety centres", exact=True).wait_for()
                page.locator(".places-table .link-button").first.click()
                page.locator(".place-card").wait_for()
            elif key == "map":
                page.locator("#status-map canvas").first.wait_for()
                page.locator(".problem-list button").first.click()
            else:
                page.get_by_label("Search shared records", exact=True).wait_for()
            audit("tool-" + key)
        # Communications: the templates list with one template open, and the media recipients.
        page.get_by_role("group", name="Communications", exact=True).get_by_role("button", name="Templates", exact=True).click()
        page.locator(".places-table .link-button").first.click()
        page.locator(".place-card").wait_for()
        audit("communications-templates")
        page.get_by_role("group", name="Communications", exact=True).get_by_role("button", name="Media recipients", exact=True).click()
        page.get_by_label("Search Media recipients", exact=True).wait_for()
        audit("communications-media")
        # Public status page: no login; English, Sinhala and a phone in Tamil.
        page.goto(server.url + "/public?area=ds-nuwara-eliya-kothmale")
        page.locator("#area h2").wait_for()
        audit("public-status")
        page.get_by_role("button", name="Ask for help in this area", exact=True).click()
        audit("public-ask-dialog")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Track a request", exact=True).click()
        audit("public-track-dialog")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="සිංහල", exact=True).click()
        audit("public-status-sinhala")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(server.url + "/public?area=ds-nuwara-eliya-walapane&lang=ta")
        page.locator("#area h2").wait_for()
        audit("public-status-tamil-mobile")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(server.url + "/#view=incidents")
        page.locator(".incident-card").first.wait_for()
        audit("incident-board")
        page.get_by_role("button", name="Collapse sidebar", exact=True).click()
        audit("sidebar-rail")
        page.get_by_role("button", name="Workspace settings", exact=True).click()
        audit("navigation-settings")
        page.keyboard.press("Escape")
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Show navigation", exact=True).click()
        audit("mobile-navigation")
        # Labelled visual-only fixtures; never submitted as evidence or progress.
        page.goto(server.url + "/#view=communications")
        page.wait_for_load_state("networkidle")
        page.evaluate("""() => {
          const fixture = document.createElement('lm-work-activity');
          fixture.id = 'activity-audit-fixture';
          document.querySelector('#main').prepend(fixture);
          fixture.expanded = true;
          fixture.turn = {id:'audit-ui-fixture-only', status:'running', steps:[
            {label:'UI audit fixture: reading saved reports',status:'completed'},
            {label:'UI audit fixture: preparing a response',status:'running'}]};
        }""")
        audit("activity-running-ui-fixture")
        for status in ["failed", "interrupted"]:
            page.locator("#activity-audit-fixture").evaluate("(el,status) => {el.turn={...el.turn,status};}", status)
            audit("activity-" + status + "-ui-fixture")
        browser.close()
    result = {"scope": "Fictional offline local screens only; no manual screen-reader or full WCAG conformance claim", "screens": checks, "total_violations": sum(len(s["violations"]) for s in checks)}
    (args.output_dir / "axe-results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    raise SystemExit(1 if result["total_violations"] else 0)


if __name__ == "__main__":
    main()
