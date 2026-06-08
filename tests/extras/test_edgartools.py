import logging
import os
import sys

import pytest

logger = logging.getLogger(__name__)


@pytest.mark.skipif(sys.implementation.name == "pypy", reason="Not supported on PyPy")
def test_edgartools_1(tmp_path, caplog):
    """Simple test to make sure changes doesn't blow up edgartools"""
    os.environ["EDGAR_LOCAL_DATA_DIR"] = str(tmp_path)

    from edgar import Company, core

    # edgartools pins the httpxthrottlecache logger to WARNING (edgar/core.py), which
    # suppresses the INFO lines this test looks for. Re-enable capture for that logger.
    caplog.set_level(logging.INFO, logger="httpxthrottlecache")

    logger.info("edgartools using get_edgar_data_directory=%s", core.get_edgar_data_directory())
    company = Company("MSFT")

    filings = company.get_filings()

    assert len(filings) >= 1000

    assert "httpxthrottlecache" in caplog.text
    # TODO: Check for hits and misses
