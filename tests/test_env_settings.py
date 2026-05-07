from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jessa_app import env_settings


class EnvSettingsTests(unittest.TestCase):
    def test_listener_settings_are_written_to_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OPENAI_MODEL=gpt-5.4-mini\n# keep this comment\n", encoding="utf-8")

            with patch.object(env_settings, "env_path", return_value=env_path):
                payload = env_settings.update_env_values(
                    {
                        "JESSA_HOST": "0.0.0.0",
                        "JESSA_PORT": "9122",
                    }
                )

            content = env_path.read_text(encoding="utf-8")
            self.assertIn("# keep this comment", content)
            self.assertIn("JESSA_HOST=0.0.0.0", content)
            self.assertIn("JESSA_PORT=9122", content)

            access_fields = {
                field["name"]: field
                for group in payload["groups"]
                if group["name"] == "Access"
                for field in group["fields"]
            }
            self.assertEqual(access_fields["JESSA_PORT"]["kind"], "number")


if __name__ == "__main__":
    unittest.main()
