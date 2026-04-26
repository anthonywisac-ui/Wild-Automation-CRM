"""
Pytest configuration for Wild CRM UI tests.

Run headed:   pytest tests/ -v --headed
Slow motion:  pytest tests/ -v --slowmo=500
"""

import os
import pytest


def pytest_configure(config):
    # Mirror --headed (owned by pytest-playwright) into env var for test module
    if config.getoption("--headed", default=False):
        os.environ["HEADED"] = "1"
    slowmo = config.getoption("--slowmo", default=0)
    if slowmo:
        os.environ["SLOWMO"] = str(slowmo)
