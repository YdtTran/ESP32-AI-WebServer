#!/usr/bin/env python3
"""
Interactive / End-to-End Live Verification Script for ThingsBoard MCP Server.
Connects an MCP client to the ThingsBoard MCP Server over stdio,
resolves credentials from the active Agent Profile, and performs read-only checks:
1. list_devices
2. get_telemetry
3. discover_capabilities
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run_live_test(profile_path: str = "agent_profile.example.json"):
    print("=" * 65)
    print("ThingsBoard MCP Server - Live Verification Suite")
    print("=" * 65)
    print(f"Target Agent Profile: {profile_path}\n")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.server", "--profile", profile_path],
        env={
            **os.environ,
            "PYTHONPATH": project_root,
            "LOG_LEVEL": "INFO",
        }
    )

    print("[1/5] Launching MCP Server via stdio transport...")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            print("[2/5] Initializing MCP session...")
            init_res = await session.initialize()
            print(f"  -> Connected to server: {init_res.server_info.name} (protocol: {init_res.protocol_version})\n")

            print("[3/5] Discovering tools via session.list_tools()...")
            tools_res = await session.list_tools()
            for t in tools_res.tools:
                print(f"  • Tool: {t.name:15} | Description: {t.description.splitlines()[0]}")
            print()

            # Test Tool 1: list_devices
            print("[4/5] Calling Tool 1: 'list_devices'...")
            call_res = await session.call_tool("list_devices", {"page_size": 5})
            list_text = call_res.content[0].text
            print(f"  Output:\n{list_text}\n")
            list_data = json.loads(list_text)

            target_device = "Virtual-Environmental-Monitor"
            devices = list_data.get("devices", [])
            if devices:
                target_device = devices[0]["name"]
                print(f"  -> Selecting device for tests: '{target_device}' (ID: {devices[0]['id']})")

            # Test Tool 2: get_telemetry
            print(f"\n[5/5] Calling Tool 2: 'get_telemetry' for '{target_device}'...")
            telem_res = await session.call_tool("get_telemetry", {
                "device_identifier": target_device,
                "keys": ["temperature", "humidity", "co2", "battery"]
            })
            telem_text = telem_res.content[0].text
            print(f"  Output:\n{telem_text}\n")

            print("[6/6] Calling 'discover_capabilities' without executing RPC...")
            discovered = await session.call_tool("discover_capabilities", {})
            print(f"  Output:\n{discovered.content[0].text}\n")

    print("=" * 65)
    print("Read-only checks completed; inspect the output for errors.")
    print("=" * 65)


if __name__ == "__main__":
    profile = sys.argv[1] if len(sys.argv) > 1 else "agent_profile.example.json"
    asyncio.run(run_live_test(profile))
