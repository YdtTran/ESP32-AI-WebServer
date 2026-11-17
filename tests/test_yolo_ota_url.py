import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'firmware/main/boards/yolo-uno'


class YoloOtaUrlTests(unittest.TestCase):
    def test_board_default_uses_bridge_ota_endpoint(self):
        self.assertIn('CONFIG_OTA_URL="http://192.168.1.7:8005/xiaozhi/ota/"',
                      (BOARD / 'sdkconfig.defaults').read_text())

    def test_legacy_saved_url_is_migrated_without_overriding_custom_url(self):
        source = r'''
#include <cassert>
#include "ota_url.h"
int main() {
    const std::string fallback = "http://192.168.1.7:8005/xiaozhi/ota/";
    assert(YoloUnoOtaUrl("", fallback) == fallback);
    assert(YoloUnoOtaUrl("", "http://192.168.1.7:8003/xiaozhi/ota/") == fallback);
    assert(YoloUnoOtaUrl("http://192.168.1.7:8003/xiaozhi/ota/", fallback) == fallback);
    assert(YoloUnoOtaUrl("http://192.168.1.7:8003/xiaozhi/ota", fallback) == fallback);
    assert(YoloUnoOtaUrl("https://custom.example/xiaozhi/ota/", fallback) == "https://custom.example/xiaozhi/ota/");
    assert(YoloUnoOtaUrl("http://192.168.1.8:8003/xiaozhi/ota/", fallback) == "http://192.168.1.8:8003/xiaozhi/ota/");
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'ota-test'
            subprocess.run(['c++', '-std=c++11', '-I', str(BOARD), '-x', 'c++', '-', '-o', str(binary)],
                           input=source, text=True, check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)
