"""
Pytest configuration for Wild CRM UI tests.

Registers the --headed and --slowmo CLI flags so you can run:
    pytest tests/ -v --headed
    pytest tests/ -v --slowmo=500
"""

import os
import pytest


def pytest_addoption(parser):
    parser.addoption("--headed", action="store_true", default=False, help="Run browser in headed mode")
    parser.addoption("--slowmo", type=int, default=0, help="Slow down Playwright actions by N ms")


def pytest_configure(config):
    # Propagate CLI flags to env vars read by the test module
    if config.getoption("--headed", default=False):
        os.environ["HEADED"] = "1"
    slowmo = config.getoption("--slowmo", default=0)
    if slowmo:
        os.environ["SLOWMO"] = str(slowmo)
