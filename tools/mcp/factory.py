from .registry import MCPToolRegistry
from .report_tools import (
    CustomReportTool,
    LatestReportTool,
    SemanticArticleTool,
    SmartReportTool,
)


def create_default_registry() -> MCPToolRegistry:
    return MCPToolRegistry(
        tools=[
            LatestReportTool(),
            CustomReportTool(),
            SmartReportTool(),
            SemanticArticleTool(),
        ]
    )
