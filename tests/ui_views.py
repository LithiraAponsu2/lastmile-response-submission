"""Open incident views on the simplified screen: three tabs, the rest under More."""


def show(page, label, exact=True):
    """Select a tab by name, opening it from the More menu when it is not one of the everyday tabs."""
    tab = page.get_by_role("tab", name=label, exact=exact)
    if not tab.count():
        page.locator(".more-views > .more-trigger").click()
        page.locator(".more-views").get_by_role("menuitem", name=label, exact=exact).click()
        tab = page.get_by_role("tab", name=label, exact=exact)
    tab.click()
    return tab


def manage(page):
    """Open Overview's Incident management section (lifecycle, participants, replay clock)."""
    show(page, "Overview")
    section = page.locator("details.incident-admin")
    if not section.evaluate("el => el.open"):
        section.locator(":scope > summary").click()
    return section
