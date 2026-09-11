import json
from pathlib import Path

import pytest

from mind.tools import DEFAULT_TOOLS, ResolveURLTool, SteamCatalogTool, WebSearchTool


def test_tool_protocol_and_default_tools_are_exposed():
    assert "resolve_url" in DEFAULT_TOOLS
    assert "steam_catalog" in DEFAULT_TOOLS
    assert "web_search" in DEFAULT_TOOLS
    assert DEFAULT_TOOLS["steam_catalog"].description


def test_tool_results_include_provenance_and_cache_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KIT_TOOL_CACHE_DIR", str(tmp_path))
    tool = SteamCatalogTool()
    result = tool.run(query="Portal", app_id=None)

    assert result["ok"] is False or "tool" in result
    assert result.get("source") == "steam_catalog"
    assert "fetched_at" in result
    assert "args" in result


def test_web_search_disabled_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KIT_TOOL_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    tool = WebSearchTool()
    result = tool.run(query="The Matrix")

    assert result["ok"] is False
    assert "disabled" in result["error"].lower()


def test_resolve_url_supports_playlist_links(monkeypatch, tmp_path):
    monkeypatch.setenv("KIT_TOOL_CACHE_DIR", str(tmp_path))
    tool = ResolveURLTool()
    result = tool.run(url="https://open.spotify.com/playlist/abc123")

    assert result["ok"] is True
    assert result["data"]["kind"] == "playlist"
    assert "tracks" in result["data"]
