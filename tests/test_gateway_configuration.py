import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from gateway import server
from services import mcp_gateway


class GatewayConfigurationTests(unittest.TestCase):
    def test_gateway_fails_closed_without_key(self):
        with patch.object(server, 'API_KEY', ''):
            response = TestClient(server.app).get('/v1/models')
        self.assertEqual(response.status_code, 503)

    def test_mcp_gateway_requires_its_configured_key(self):
        with patch.object(mcp_gateway, 'API_KEY', ''):
            with self.assertRaises(HTTPException) as result:
                mcp_gateway.verify_api_key('Bearer anything')
            self.assertEqual(result.exception.status_code, 503)
        with patch.object(mcp_gateway, 'API_KEY', 'machine-key'):
            mcp_gateway.verify_api_key('Bearer machine-key')
            with self.assertRaises(HTTPException):
                mcp_gateway.verify_api_key('Bearer wrong-key')


if __name__ == '__main__':
    unittest.main()
