"""
Отправка факта вызова инструмента в журнал samui-guide.

Зачем: панель /cms/metrika/ в CMS показывает владельцу, что агент спрашивал у
Метрики. Django-таблица живёт в прод-базе, наружу не открытой, поэтому сюда
ходить она не может — вместо этого мы сами шлём POST на её эндпоинт
/api/metrika/log/.

Что отправляется и чего не отправляется. Только факт: имя инструмента,
параметры вызова, успех или ошибка, длительность. **Содержимое ответов
Метрики не отправляется** — ни целиком, ни фрагментами. Журнал отвечает на
вопрос «что агент спрашивал», а не «что ему ответили»; дублировать статистику
сайта в третье место незачем, и чем меньше её копий, тем меньше поверхность.

Три свойства, ради которых модуль устроен именно так:

  * ЛОГИРОВАНИЕ НЕ ЛОМАЕТ ИНСТРУМЕНТ. Любая ошибка отправки — сеть, таймаут,
    отказ эндпоинта, неожиданное исключение — гасится здесь и уходит в stderr.
    Агент получает результат в любом случае.
  * ЛОГИРОВАНИЕ НЕ ПОДВЕШИВАЕТ ОТВЕТ. Таймаут по умолчанию 3 секунды, и есть
    предохранитель: после трёх подряд неудач отправка выключается до конца
    жизни процесса. Иначе при лежащем Django каждый вызов инструмента
    оплачивал бы таймаут заново.
  * СЕКРЕТ ОТДЕЛЬНЫЙ. METRIKA_LOG_SECRET — это НЕ токен Метрики
    (YANDEX_API_KEY). Разное назначение и разная цена компрометации: первый
    позволяет засорить журнал, второй открывает всю статистику сайта.

Конфигурация (всё через окружение, ничего не захардкожено):

    METRIKA_LOG_ENDPOINT_URL   полный URL, например
                               https://samuiguide.ru/api/metrika/log/
    METRIKA_LOG_SECRET         значение заголовка X-Metrika-Log-Secret
    METRIKA_LOG_TIMEOUT        секунды, по умолчанию 3.0

Не задан URL или секрет — модуль молча выключен: локальная разработка не
должна требовать поднятого Django.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import time
from typing import Any, Callable

import httpx

logger = logging.getLogger("ya-metrics.call-log")

DEFAULT_TIMEOUT_SECONDS = 3.0

# После стольких подряд неудач перестаём пытаться до перезапуска процесса.
FAILURE_LIMIT = 3

# Эндпоинт отвергает ключи, похожие на секреты (см. apps/core/metrika_api.py).
# Отсеиваем их здесь же, чтобы не ловить 400 на том, что можно не отправлять.
SECRET_LIKE_KEY_RE = re.compile(
    r"token|secret|password|passwd|api[_-]?key|authorization|credential", re.I
)

# Ограничения эндпоинта — держим синхронно, чтобы не получать 400 из-за длины.
MAX_ERROR_CHARS = 2000

_consecutive_failures = 0
_disabled_reported = False


def _config() -> tuple[str, str, float] | None:
    url = os.environ.get("METRIKA_LOG_ENDPOINT_URL", "").strip()
    secret = os.environ.get("METRIKA_LOG_SECRET", "").strip()
    if not url or not secret:
        return None
    try:
        timeout = float(os.environ.get("METRIKA_LOG_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return url, secret, timeout


def _safe_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    """
    Параметры вызова, пригодные для отправки.

    Выбрасывает ctx (объект FastMCP, не сериализуется и журналу не нужен),
    пустые значения и всё, что похоже на секрет. Несериализуемое приводит к
    строке — лучше отправить repr, чем уронить логирование на JSON-кодировке.
    """
    safe: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key == "ctx" or value is None:
            continue
        if SECRET_LIKE_KEY_RE.search(key):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


async def _send(payload: dict[str, Any]) -> None:
    """
    Отправляет запись. Все ошибки гасит — вызывающему знать о них незачем.

    Предохранитель считает подряд идущие неудачи: при лежащем Django без него
    каждый вызов инструмента оплачивал бы таймаут заново.
    """
    global _consecutive_failures, _disabled_reported

    config = _config()
    if config is None:
        return

    if _consecutive_failures >= FAILURE_LIMIT:
        if not _disabled_reported:
            logger.warning(
                "отправка журнала выключена до перезапуска: %s неудач подряд",
                _consecutive_failures,
            )
            _disabled_reported = True
        return

    url, secret, timeout = config
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url, json=payload, headers={"X-Metrika-Log-Secret": secret}
            )
        if response.status_code >= 400:
            # 401 — не тот секрет, 400 — не та схема, 503 — приём выключен.
            # Всё это чинится на стороне конфигурации, а не повтором запроса.
            _consecutive_failures += 1
            logger.warning(
                "журнал не принят: HTTP %s %s",
                response.status_code,
                response.text[:200],
            )
            return
        _consecutive_failures = 0
    except Exception as exc:
        _consecutive_failures += 1
        logger.warning("журнал не отправлен (%s): %s", type(exc).__name__, exc)


def log_call(fn: Callable) -> Callable:
    """
    Декоратор инструмента: отправляет факт вызова, не влияя на его результат.

    Ставится ПОД @mcp.tool, чтобы зарегистрированным оказался уже обёрнутый
    вызов. functools.wraps сохраняет имя, докстринг и аннотации — FastMCP
    строит схему параметров по ним, и без wraps инструмент лишился бы описания
    аргументов.

    Исключение инструмента пробрасывается дальше как есть: агент должен
    увидеть настоящую ошибку, а не наш пересказ.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        payload: dict[str, Any] = {
            "tool": fn.__name__,
            "params": _safe_params(kwargs),
            "source": "mcp",
        }
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            payload.update(
                status="error",
                error=f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS],
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            await _send(payload)
            raise

        payload.update(
            status="ok",
            # Содержимое ответа НЕ отправляем — только его объём. Журнал
            # фиксирует, что вызов состоялся и что-то вернул, а сами данные
            # Метрики остаются в одном месте.
            result_summary=f"ответ получен: {len(result) if isinstance(result, str) else '—'} символов",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        await _send(payload)
        return result

    return wrapper


def reset_failure_state() -> None:
    """Сброс предохранителя. Нужен тестам, в рабочем коде не вызывается."""
    global _consecutive_failures, _disabled_reported
    _consecutive_failures = 0
    _disabled_reported = False


__all__ = ["log_call", "reset_failure_state"]
