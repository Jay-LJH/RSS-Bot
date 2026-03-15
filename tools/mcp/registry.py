from __future__ import annotations

from typing import Any

from .base import MCPTool


class MCPToolRegistry:
    def __init__(self, tools: list[MCPTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def list_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_schema() for tool in self._tools.values()]

    def execute(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        tool = self._tools.get(tool_name)
        if not tool:
            raise RuntimeError(f"未知工具：{tool_name}")
        return tool.run(arguments or {})
