from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from crawler import get_available_modules, get_report, get_smart_report


class MCPTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    @abstractmethod
    def run(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError


class LatestReportTool(MCPTool):
    name = "get_latest_report"
    description = "获取当前默认模块的最新推送"
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
        },
        "required": [],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, Any]) -> str:
        limit = int(arguments.get("limit", 3))
        return get_report(modules=None, limit=limit)


class CustomReportTool(MCPTool):
    name = "get_custom_report"
    description = "按指定模块组合返回日报，可选模块来自当前 sources.yml"
    parameters = {
        "type": "object",
        "properties": {
            "modules": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
        },
        "required": ["modules"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, Any]) -> str:
        modules = arguments.get("modules") or get_available_modules()
        limit = int(arguments.get("limit", 3))
        return get_report(modules=[str(m) for m in modules], limit=limit)


class SmartReportTool(MCPTool):
    name = "get_smart_report"
    description = "根据用户问题自动识别最相关的信息源并推送"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "用户原始问题或意图"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip()
        limit = int(arguments.get("limit", 3))
        return get_smart_report(query=query, limit=limit)


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


def create_default_registry() -> MCPToolRegistry:
    return MCPToolRegistry(
        tools=[
            LatestReportTool(),
            CustomReportTool(),
            SmartReportTool(),
        ]
    )
