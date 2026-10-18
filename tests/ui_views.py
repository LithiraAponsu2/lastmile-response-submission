"""Open incident views on the simplified screen: five everyday tabs, the rest under More."""
import re


def show(page, label, exact=True):
    """Select a tab by name, opening it from the More menu when it is not one of the everyday tabs."""
    # A partial name matches from the start, so "Requests" never picks "Public requests".
    name = label if exact else re.compile("^" + re.escape(label))
    tab = page.get_by_role("tab", name=name, exact=exact or None)
    if not tab.count():
        page.locator(".more-views > .more-trigger").click()
        page.locator(".more-views").get_by_role("menuitem", name=name, exact=exact or None).click()
        tab = page.get_by_role("tab", name=name, exact=exact or None)
    tab.click()
    return tab


def manage(page):
    """Open Overview's Incident management section (lifecycle, participants, replay clock)."""
    show(page, "Overview")
    section = page.locator("details.incident-admin")
    if not section.evaluate("el => el.open"):
        section.locator(":scope > summary").click()
    return section
