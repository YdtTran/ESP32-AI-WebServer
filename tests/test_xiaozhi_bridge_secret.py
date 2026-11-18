"""Check that both containers can share a secret without changing data/.config.yaml."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "xiaozhi-runtime"))
from config import config_loader
from core.utils.cache.manager import cache_manager


class BridgeSecretTest(unittest.TestCase):
    def test_environment_secret_overrides_file(self):
        local = {"manager-api": {"url": "http://bridge", "secret": "placeholder"}}
        fetch = AsyncMock(return_value={})
        with patch.dict(os.environ, {"BRIDGE_SECRET": "shared-secret"}), \
             patch.object(cache_manager, "get", return_value=None), \
             patch.object(cache_manager, "set"), \
             patch.object(config_loader, "read_config", side_effect=[{}, local]), \
             patch.object(config_loader, "get_config_from_api_async", fetch), \
             patch.object(config_loader, "ensure_directories"):
            config_loader.load_config()
        self.assertEqual(fetch.await_args.args[0]["manager-api"]["secret"], "shared-secret")


if __name__ == "__main__":
    unittest.main()
