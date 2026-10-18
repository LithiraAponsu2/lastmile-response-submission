"""Shared Chromium for the browser suites, with no external map or messaging calls."""
import os

import pytest

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        executable = os.environ.get("LASTMILE_E2E_CHROMIUM")
        if not executable:
            pytest.skip("Set LASTMILE_E2E_CHROMIUM to run the browser checks")
        browser = p.chromium.launch(executable_path=executable, headless=True)
        yield browser
        browser.close()
