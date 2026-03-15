from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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
