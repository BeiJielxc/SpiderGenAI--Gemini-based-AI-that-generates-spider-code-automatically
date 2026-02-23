"""
Dynamic tool registry with discovery, filtering, and execution routing.
"""

from __future__ import annotations

import difflib
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set

try:
    from tools import (
        ToolContext,
        ToolResult,
        tool_analyze_page,
        tool_critic_validate,
        tool_generate_crawler_code,
        tool_get_intercepted_apis,
        tool_get_network_requests,
        tool_get_page_html,
        tool_get_page_info,
        tool_get_site_menu_tree,
        tool_install_python_packages,
        tool_open_page,
        tool_probe_navigation,
        tool_run_python_snippet,
        tool_scroll_page,
        tool_smart_date_api_scan,
        tool_take_screenshot,
        tool_wait_for_network_idle,
        tool_detect_data_status,
        tool_enhanced_page_analysis,
        tool_build_verified_category_mapping,
        tool_validate_code,
    )
    from high_level_tools import (
        tool_extract_list_and_pagination,
        tool_capture_api_and_infer_params,
        tool_turn_page_and_verify_change,
        tool_probe_detail_page,
    )
except ImportError:
    from .tools import (  # type: ignore
        ToolContext,
        ToolResult,
        tool_analyze_page,
        tool_critic_validate,
        tool_generate_crawler_code,
        tool_get_intercepted_apis,
        tool_get_network_requests,
        tool_get_page_html,
        tool_get_page_info,
        tool_get_site_menu_tree,
        tool_install_python_packages,
        tool_open_page,
        tool_probe_navigation,
        tool_run_python_snippet,
        tool_scroll_page,
        tool_smart_date_api_scan,
        tool_take_screenshot,
        tool_wait_for_network_idle,
        tool_detect_data_status,
        tool_enhanced_page_analysis,
        tool_build_verified_category_mapping,
        tool_validate_code,
    )
    from .high_level_tools import (  # type: ignore
        tool_extract_list_and_pagination,
        tool_capture_api_and_infer_params,
        tool_turn_page_and_verify_change,
        tool_probe_detail_page,
    )


ToolHandler = Callable[..., Awaitable[ToolResult]]
AvailabilityCheck = Callable[[ToolContext], bool]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler
    enabled: bool = True
    tags: Set[str] = field(default_factory=set)
    run_modes: Optional[Set[str]] = None
    risk_level: str = "low"
    availability_check: Optional[AvailabilityCheck] = None


class ToolRegistry:
    """Runtime registry for dynamic tool ecosystems."""

    def __init__(self, fallback_map: Optional[Dict[str, List[str]]] = None):
        self._tools: Dict[str, RegisteredTool] = {}
        self._fallback_map = fallback_map or {}

    def register_tool(
        self,
        spec: ToolSpec,
        handler: ToolHandler,
        *,
        enabled: bool = True,
        tags: Optional[Iterable[str]] = None,
        run_modes: Optional[Iterable[str]] = None,
        risk_level: str = "low",
        availability_check: Optional[AvailabilityCheck] = None,
    ) -> None:
        self._tools[spec.name] = RegisteredTool(
            spec=spec,
            handler=handler,
            enabled=enabled,
            tags=set(tags or []),
            run_modes=set(run_modes) if run_modes else None,
            risk_level=risk_level,
            availability_check=availability_check,
        )

    def unregister_tool(self, name: str) -> None:
        self._tools.pop(name, None)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get_registered(self, name: str) -> Optional[RegisteredTool]:
        return self._tools.get(name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        tool = self._tools.get(name)
        if tool:
            tool.enabled = enabled

    def resolve_tools(self, ctx: ToolContext, include_disabled: bool = False) -> List[RegisteredTool]:
        resolved: List[RegisteredTool] = []
        for tool in self._tools.values():
            if not include_disabled and not tool.enabled:
                continue
            if tool.run_modes and ctx.run_mode not in tool.run_modes:
                continue
            if tool.availability_check:
                try:
                    if not tool.availability_check(ctx):
                        continue
                except Exception:
                    continue
            resolved.append(tool)
        resolved.sort(key=lambda t: t.spec.name)
        return resolved

    def list_tool_names(self, ctx: ToolContext) -> List[str]:
        return [t.spec.name for t in self.resolve_tools(ctx)]

    def get_tools_prompt(self, ctx: ToolContext, include_risk: bool = True) -> str:
        lines: List[str] = []
        for tool in self.resolve_tools(ctx):
            params = tool.spec.parameters or {}
            params_desc = []
            for key, schema in params.items():
                item = f"- {key} ({schema.get('type', 'any')})"
                if "default" in schema:
                    item += f" [default={schema['default']}]"
                if "description" in schema:
                    item += f": {schema['description']}"
                params_desc.append(item)

            header = f"### {tool.spec.name}"
            if include_risk:
                header += f" (risk={tool.risk_level})"
            body = tool.spec.description
            if params_desc:
                body += "\n" + "\n".join(params_desc)
            else:
                body += "\n(no parameters)"
            lines.append(header + "\n" + body)

        return "\n\n".join(lines)

    def get_fallback_tools(self, tool_name: str, tool_result: Optional[ToolResult] = None) -> List[str]:
        if tool_result and tool_result.suggested_next_tools:
            return tool_result.suggested_next_tools
        return self._fallback_map.get(tool_name, [])

    async def execute_tool(self, ctx: ToolContext, tool_name: str, tool_input: Dict[str, Any]) -> ToolResult:
        tool = self._tools.get(tool_name)
        if not tool:
            alternatives = difflib.get_close_matches(tool_name, list(self._tools.keys()), n=4, cutoff=0.35)
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                summary=f"Unknown tool: {tool_name}",
                error_code="unknown_tool",
                recoverable=True,
                suggested_next_tools=alternatives,
            )

        if not tool.enabled:
            return ToolResult(
                success=False,
                error=f"Tool is disabled: {tool_name}",
                summary=f"Tool is disabled: {tool_name}",
                error_code="tool_disabled",
                recoverable=True,
                suggested_next_tools=self.get_fallback_tools(tool_name),
            )

        if tool.run_modes and ctx.run_mode not in tool.run_modes:
            return ToolResult(
                success=False,
                error=f"Tool {tool_name} not available for run_mode={ctx.run_mode}",
                summary=f"Tool {tool_name} is run_mode-restricted",
                error_code="run_mode_mismatch",
                recoverable=True,
                suggested_next_tools=self.get_fallback_tools(tool_name),
            )

        if tool.availability_check:
            try:
                if not tool.availability_check(ctx):
                    return ToolResult(
                        success=False,
                        error=f"Tool {tool_name} is not currently available",
                        summary=f"Tool {tool_name} unavailable in current context",
                        error_code="tool_unavailable",
                        recoverable=True,
                        suggested_next_tools=self.get_fallback_tools(tool_name),
                    )
            except Exception as exc:
                return ToolResult(
                    success=False,
                    error=f"Availability check failed for {tool_name}: {exc}",
                    summary=f"Availability check failed for {tool_name}",
                    error_code="availability_check_failed",
                    recoverable=True,
                    suggested_next_tools=self.get_fallback_tools(tool_name),
                )

        try:
            if not isinstance(tool_input, dict):
                tool_input = {}
            result = await tool.handler(ctx, **tool_input)
            if not isinstance(result, ToolResult):
                result = ToolResult(success=False, error="Tool returned invalid result type", error_code="invalid_tool_result")
            if not result.success and not result.suggested_next_tools:
                result.suggested_next_tools = self.get_fallback_tools(tool_name, result)
            return result
        except TypeError as exc:
            return ToolResult(
                success=False,
                error=f"Invalid parameters for {tool_name}: {exc}",
                summary=f"Invalid parameters for {tool_name}",
                error_code="invalid_params",
                recoverable=True,
                suggested_next_tools=self.get_fallback_tools(tool_name),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Tool {tool_name} failed: {exc}\n{traceback.format_exc()}",
                summary=f"Tool {tool_name} raised exception",
                error_code="tool_execution_exception",
                recoverable=True,
                suggested_next_tools=self.get_fallback_tools(tool_name),
            )


def _has_executor(ctx: ToolContext) -> bool:
    return getattr(ctx, "executor_session", None) is not None


def _has_critic(ctx: ToolContext) -> bool:
    return getattr(ctx, "critic", None) is not None


def create_default_tool_registry() -> ToolRegistry:
    """Create registry with built-in atomic/high-level tools."""
    fallback_map = {
        "open_page": ["take_screenshot", "extract_list_and_pagination"],
        "scroll_page": ["extract_list_and_pagination", "get_network_requests"],
        "analyze_page": ["take_screenshot", "get_network_requests"],
        "wait_for_network_idle": ["get_network_requests", "analyze_page"],
        "get_intercepted_apis": ["get_network_requests", "capture_api_and_infer_params"],
        "detect_data_status": ["extract_list_and_pagination", "enhanced_page_analysis"],
        "extract_list_and_pagination": ["capture_api_and_infer_params", "analyze_page"],
        "capture_api_and_infer_params": ["extract_list_and_pagination", "analyze_page"],
        "turn_page_and_verify_change": ["extract_list_and_pagination", "generate_crawler_code"],
        "probe_detail_page": ["generate_crawler_code", "extract_list_and_pagination"],
        "enhanced_page_analysis": ["analyze_page", "get_network_requests"],
        "build_verified_category_mapping": ["probe_navigation", "generate_crawler_code"],
        "smart_date_api_scan": ["take_screenshot", "probe_navigation", "analyze_page"],
        "probe_navigation": ["get_site_menu_tree", "analyze_page"],
        "generate_crawler_code": ["analyze_page", "get_network_requests", "validate_code"],
        "validate_code": ["generate_crawler_code", "critic_validate"],
        "run_python_snippet": ["generate_crawler_code", "validate_code"],
        "install_python_packages": ["run_python_snippet", "generate_crawler_code"],
        "critic_validate": ["generate_crawler_code", "validate_code"],
    }
    registry = ToolRegistry(fallback_map=fallback_map)

    registry.register_tool(
        ToolSpec(
            name="open_page",
            description="Open a target URL. Usually the first step.",
            parameters={
                "url": {"type": "string", "description": "Target URL. Empty means use user URL."},
                "wait_until": {"type": "string", "default": "domcontentloaded", "description": "domcontentloaded | networkidle"},
            },
        ),
        tool_open_page,
        tags={"atomic", "browser"},
    )
    registry.register_tool(
        ToolSpec(
            name="scroll_page",
            description="Scroll page to trigger lazy loading.",
            parameters={"times": {"type": "integer", "default": 3, "description": "Scroll count"}},
        ),
        tool_scroll_page,
        tags={"atomic", "browser"},
    )
    registry.register_tool(
        ToolSpec(name="get_page_info", description="Get basic page info like title and URL.", parameters={}),
        tool_get_page_info,
        tags={"atomic", "inspect"},
    )
    registry.register_tool(
        ToolSpec(name="get_page_html", description="Capture full page HTML for later code generation.", parameters={}),
        tool_get_page_html,
        tags={"atomic", "inspect"},
    )
    registry.register_tool(
        ToolSpec(
            name="extract_list_and_pagination",
            description=(
                "Auto-discover the news/data list on the current page. "
                "Returns list items (title/url/date) with auto-detected CSS selectors, "
                "pagination controls (next/prev/pageNums), and date-range hints. "
                "Use this as the FIRST exploration step after open_page to understand page structure. "
                "No need to specify selectors – the tool finds the best candidate automatically."
            ),
            parameters={},
        ),
        tool_extract_list_and_pagination,
        tags={"high_level", "extract", "pagination"},
    )
    registry.register_tool(
        ToolSpec(name="analyze_page", description="Analyze structure, links, and captured API signals.", parameters={}),
        tool_analyze_page,
        tags={"atomic", "analyze"},
    )
    registry.register_tool(
        ToolSpec(name="take_screenshot", description="Capture page screenshot (base64).", parameters={}),
        tool_take_screenshot,
        tags={"atomic", "visual"},
    )
    registry.register_tool(
        ToolSpec(name="get_network_requests", description="Get captured API/XHR/fetch requests.", parameters={}),
        tool_get_network_requests,
        tags={"atomic", "network"},
    )
    registry.register_tool(
        ToolSpec(
            name="wait_for_network_idle",
            description="Wait until network becomes idle after interactions.",
            parameters={
                "timeout": {"type": "number", "default": 5.0, "description": "Max wait seconds"},
                "idle_time": {"type": "number", "default": 0.5, "description": "Required idle window in seconds"},
            },
        ),
        tool_wait_for_network_idle,
        tags={"atomic", "network"},
    )
    registry.register_tool(
        ToolSpec(
            name="get_intercepted_apis",
            description="Return APIs captured by browser-level interceptors.",
            parameters={},
        ),
        tool_get_intercepted_apis,
        tags={"atomic", "network"},
    )
    registry.register_tool(
        ToolSpec(
            name="detect_data_status",
            description="Detect whether page has data/empty/error/loading status.",
            parameters={},
        ),
        tool_detect_data_status,
        tags={"atomic", "analyze"},
    )
    registry.register_tool(
        ToolSpec(
            name="capture_api_and_infer_params",
            description=(
                "Sniff XHR/Fetch APIs by performing interactions (click next page, scroll). "
                "Automatically finds the data-bearing API, parses response arrays, and infers "
                "which parameters control page/category/date. Use this when extract_list_and_pagination "
                "finds no list (page loads data via AJAX) or you need to discover the API endpoint."
            ),
            parameters={},
        ),
        tool_capture_api_and_infer_params,
        tags={"high_level", "network", "api"},
    )
    registry.register_tool(
        ToolSpec(
            name="enhanced_page_analysis",
            description="Run browser native enhanced analysis and aggregate richer page signals.",
            parameters={},
        ),
        tool_enhanced_page_analysis,
        tags={"high_level", "analyze"},
    )
    registry.register_tool(
        ToolSpec(
            name="turn_page_and_verify_change",
            description=(
                "Navigate to the next page and VERIFY content actually changed. "
                "Accepts an optional next_url for direct navigation. If not provided, "
                "automatically tries common next-page selectors. Returns success only if "
                "page content changed after navigation. Use this instead of blind clicking."
            ),
            parameters={
                "next_url": {
                    "type": "string",
                    "default": "",
                    "description": "Direct URL for next page. Leave empty to auto-detect next-page button.",
                },
            },
        ),
        tool_turn_page_and_verify_change,
        tags={"high_level", "pagination"},
    )
    registry.register_tool(
        ToolSpec(
            name="probe_detail_page",
            description=(
                "Open ONE detail/article page in a new tab, scan for the content container "
                "(e.g. .TRS_Editor, .article-content, etc.) and title element, then close the tab. "
                "Returns contentSelector, sampleContentHtml, and structureHint for the detail page. "
                "Use this AFTER extract_list_and_pagination and BEFORE generate_crawler_code to ensure "
                "the generated crawler knows the correct detail page DOM structure. "
                "If no URL is given, automatically picks the first item from extract_list_and_pagination."
            ),
            parameters={
                "url": {
                    "type": "string",
                    "default": "",
                    "description": "Detail page URL to probe. Leave empty to auto-pick from previous extraction.",
                },
            },
        ),
        tool_probe_detail_page,
        tags={"high_level", "detail"},
    )
    registry.register_tool(
        ToolSpec(
            name="build_verified_category_mapping",
            description="Build verified category mapping from captured API evidence.",
            parameters={},
        ),
        tool_build_verified_category_mapping,
        tags={"high_level", "navigation"},
    )
    registry.register_tool(
        ToolSpec(
            name="get_site_menu_tree",
            description="Extract site menu tree for multi-section crawling.",
            parameters={"max_depth": {"type": "integer", "default": 3, "description": "Tree max depth"}},
        ),
        tool_get_site_menu_tree,
        tags={"high_level", "navigation"},
    )
    registry.register_tool(
        ToolSpec(
            name="probe_navigation",
            description="Click selected menu paths and capture API/filter mapping changes.",
            parameters={"paths": {"type": "array", "description": "Menu paths to probe; empty means all leaves."}},
        ),
        tool_probe_navigation,
        tags={"high_level", "navigation"},
    )
    registry.register_tool(
        ToolSpec(
            name="smart_date_api_scan",
            description="High-level date API detector with layered fallback strategy.",
            parameters={},
        ),
        tool_smart_date_api_scan,
        tags={"high_level", "date"},
    )
    registry.register_tool(
        ToolSpec(
            name="generate_crawler_code",
            description="Generate crawler code from collected context and selected strategy.",
            parameters={"strategy": {"type": "string", "description": "Recommended extraction strategy."}},
        ),
        tool_generate_crawler_code,
        tags={"high_level", "generation"},
    )
    registry.register_tool(
        ToolSpec(name="validate_code", description="Run static validation for generated crawler code.", parameters={}),
        tool_validate_code,
        tags={"high_level", "quality"},
    )
    registry.register_tool(
        ToolSpec(
            name="run_python_snippet",
            description="Run Python code in persistent sandbox session (if configured).",
            parameters={
                "code": {"type": "string", "description": "Python code snippet"},
                "timeout_sec": {"type": "integer", "default": 120, "description": "Execution timeout in seconds"},
            },
        ),
        tool_run_python_snippet,
        tags={"sandbox", "executor"},
        risk_level="medium",
        availability_check=_has_executor,
    )
    registry.register_tool(
        ToolSpec(
            name="install_python_packages",
            description="Install Python packages inside sandbox executor session (policy-gated).",
            parameters={
                "packages": {"type": "array", "description": "Package specifiers, e.g. ['playwright', 'pandas==2.2.2']"},
                "timeout_sec": {"type": "integer", "default": 900, "description": "Install timeout in seconds"},
            },
        ),
        tool_install_python_packages,
        tags={"sandbox", "executor", "dependencies"},
        risk_level="high",
        availability_check=_has_executor,
    )
    registry.register_tool(
        ToolSpec(
            name="critic_validate",
            description="Run rule-based (and optional LLM-assisted) acceptance validation.",
            parameters={
                "objective": {"type": "string", "description": "Task objective for validation"},
                "min_items": {"type": "integer", "default": 1, "description": "Expected minimum extracted item count"},
            },
        ),
        tool_critic_validate,
        tags={"quality", "critic"},
        availability_check=_has_critic,
    )

    return registry


__all__ = [
    "ToolSpec",
    "RegisteredTool",
    "ToolRegistry",
    "create_default_tool_registry",
]
