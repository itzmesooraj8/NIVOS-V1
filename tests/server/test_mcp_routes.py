"""Tests for MCP server and plugin routes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from nivos.server.app import create_app  # noqa: E402


@pytest.fixture
def mcp_test_client(tmp_path, monkeypatch):
    monkeypatch.setenv("NIVOS_HOME", str(tmp_path))

    engine = MagicMock()
    engine.engine_id = "mock"
    engine.health.return_value = True
    engine.list_models.return_value = ["test-model"]

    app = create_app(engine=engine, model="test-model")
    return TestClient(app), tmp_path


def test_list_empty_mcp_servers(mcp_test_client):
    client, tmp_path = mcp_test_client
    resp = client.get("/v1/mcp/servers")
    assert resp.status_code == 200
    data = resp.json()
    assert "servers" in data
    assert data["count"] == 0


def test_add_and_delete_mcp_server(mcp_test_client):
    client, tmp_path = mcp_test_client

    with patch("nivos.server.mcp_routes._connect_and_register_server") as mock_connect:
        mock_connect.return_value = ["fs_read", "fs_write"]

        payload = {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"],
            "enabled": True,
        }

        resp = client.post("/v1/mcp/servers", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["name"] == "filesystem"
        assert "fs_read" in data["discovered_tools"]

        list_resp = client.get("/v1/mcp/servers")
        assert list_resp.status_code == 200
        servers = list_resp.json()["servers"]
        assert len(servers) == 1
        assert servers[0]["name"] == "filesystem"

        del_resp = client.delete("/v1/mcp/servers/filesystem")
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] == "filesystem"

        after_del = client.get("/v1/mcp/servers")
        assert after_del.json()["count"] == 0


def test_list_plugins_endpoint(mcp_test_client):
    client, tmp_path = mcp_test_client
    resp = client.get("/v1/plugins")
    assert resp.status_code == 200
    assert "plugins" in resp.json()

