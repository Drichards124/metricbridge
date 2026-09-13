"""The package installs and imports under its declared name — the smallest proof the build is wired."""

from importlib.metadata import version

import metricbridge


def test_version_comes_from_distribution_metadata():
    assert metricbridge.__version__ == version("metricbridge")
