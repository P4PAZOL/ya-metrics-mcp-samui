"""MCP tool registrations for Yandex Metrika analytics."""
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from ya_metrics_mcp.call_log import log_call
from ya_metrics_mcp.servers.dependencies import get_metrika_fetcher
from ya_metrics_mcp.servers.main import mcp

# ─── Account & Basic Analytics ───────────────────────────────────────────────

@mcp.tool(tags={"metrika", "read"})
@log_call
async def list_counters(
    ctx: Context,
    search: Annotated[str | None, Field(description="Filter counters by name or site URL")] = None,
    per_page: Annotated[int, Field(description="Max counters to return (default 100)", ge=1, le=1000)] = 100,
) -> str:
    """List all Yandex Metrika counters available to this account. Use this to find counter IDs."""
    fetcher = await get_metrika_fetcher(ctx)
    return await fetcher.list_counters(search, per_page)


@mcp.tool(tags={"metrika", "read"})
@log_call
async def list_goals(
    ctx: Context,
    counter_id: Annotated[str, Field(description="Yandex Metrika counter ID")],
) -> str:
    """List all conversion goals configured for a counter. Use goal IDs with get_goals_conversion."""
    fetcher = await get_metrika_fetcher(ctx)
    return await fetcher.list_goals(counter_id)


@mcp.tool(tags={"metrika", "read"})
@log_call
async def get_traffic_sources_types(
    ctx: Context,
    counter_id: Annotated[str, Field(description="Counter ID")],
) -> str:
    """Analyze different types of traffic sources (organic, direct, referral)."""
    fetcher = await get_metrika_fetcher(ctx)
    return await fetcher.get_traffic_sources_types(counter_id)


@mcp.tool(tags={"metrika", "read"})
@log_call
async def get_goals_conversion(
    ctx: Context,
    counter_id: Annotated[str, Field(description="Counter ID")],
    goal_ids: Annotated[list[int], Field(description="List of goal IDs to track")],
) -> str:
    """Track conversion rates for specified goals."""
    fetcher = await get_metrika_fetcher(ctx)
    return await fetcher.get_goals_conversion(counter_id, goal_ids)


@mcp.tool(tags={"metrika", "read"})
@log_call
async def get_page_performance(
    ctx: Context,
    counter_id: Annotated[str, Field(description="Counter ID")],
    date_from: Annotated[str | None, Field(description="Start date YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, Field(description="End date YYYY-MM-DD")] = None,
) -> str:
    """Get page performance and bounce rate by URL path."""
    fetcher = await get_metrika_fetcher(ctx)
    return await fetcher.get_page_performance(counter_id, date_from, date_to)


@mcp.tool(tags={"metrika", "read"})
@log_call
async def get_regional_data(
    ctx: Context,
    counter_id: Annotated[str, Field(description="Counter ID")],
    cities: Annotated[list[str] | None, Field(description="City names to filter by")] = None,
) -> str:
    """Get sessions and users data for specific regions/cities."""
    fetcher = await get_metrika_fetcher(ctx)
    return await fetcher.get_regional_data(counter_id, cities)


@mcp.tool(tags={"metrika", "read"})
@log_call
async def get_device_analysis(
    ctx: Context,
    counter_id: Annotated[str, Field(description="Counter ID")],
    date_from: Annotated[str | None, Field(description="Start date YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, Field(description="End date YYYY-MM-DD")] = None,
) -> str:
    """Analyze user behavior by browser and operating system."""
    fetcher = await get_metrika_fetcher(ctx)
    return await fetcher.get_device_analysis(counter_id, date_from, date_to)
