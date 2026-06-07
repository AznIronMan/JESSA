from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from jessa_app import config


class ConfigTests(unittest.TestCase):
    def test_linkedin_headless_defaults_to_true_on_displayless_linux(self) -> None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "JESSA_LINKEDIN_BROWSER_HEADLESS",
            }
        }

        with patch.dict(os.environ, env, clear=True), patch("platform.system", return_value="Linux"):
            self.assertTrue(config._linkedin_browser_headless())

    def test_linkedin_headless_env_override_allows_visible_browser(self) -> None:
        with patch.dict(os.environ, {"JESSA_LINKEDIN_BROWSER_HEADLESS": "false"}, clear=False):
            self.assertFalse(config._linkedin_browser_headless())


if __name__ == "__main__":
    unittest.main()
