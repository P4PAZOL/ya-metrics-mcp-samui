"""
Гейт форка: набор инструментов зафиксирован точным списком.

Апстрим регистрирует 31 инструмент. Для samui-guide согласованы семь: пять
предметных категорий плюс два инструмента обнаружения, без которых остальные
неработоспособны (counter_id и goal_id больше взять негде — дефолтов в
конфиге нет).

Тест сравнивает множества целиком, а не проверяет вхождение: он падает и
когда нужный инструмент пропал, и когда после merge из upstream вернулся
лишний. Второе важнее — именно так набор и расползается обратно.
"""
import asyncio

import pytest

from ya_metrics_mcp.servers.main import mcp
import ya_metrics_mcp.servers.tools  # noqa: F401  — регистрация тулов при импорте

EXPECTED_TOOLS = {
    # Обнаружение
    "list_counters",
    "list_goals",
    # Источники трафика
    "get_traffic_sources_types",
    # Цели и конверсии
    "get_goals_conversion",
    # Посещаемость по страницам
    "get_page_performance",
    # География аудитории
    "get_regional_data",
    # Устройства аудитории
    "get_device_analysis",
}


def _registered() -> set[str]:
    return set(asyncio.run(mcp.get_tools()))


def test_exactly_the_agreed_tools_are_registered():
    assert _registered() == EXPECTED_TOOLS


@pytest.mark.parametrize(
    "tool",
    [
        "get_ecommerce_performance",
        "get_yandex_direct_experiment",
        "compare_segments",
        "get_drilldown",
        "get_content_analytics_articles",
    ],
)
def test_upstream_extras_stay_out(tool):
    """Выборка вырезанных инструментов — на случай возврата после merge."""
    assert tool not in _registered()


def test_server_exposes_no_write_tools():
    """
    Апстрим помечает тегами, и все оставленные инструменты должны быть read.
    Свойство проверяем по тегам, а не по именам: инструмент с безобидным
    именем может однажды получить write-тег.
    """
    tools = asyncio.run(mcp.get_tools())
    for name, tool in tools.items():
        tags = getattr(tool, "tags", set()) or set()
        assert "write" not in tags, f"{name} помечен как write"
