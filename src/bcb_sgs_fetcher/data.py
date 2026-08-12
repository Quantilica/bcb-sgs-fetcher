"""Client for the public BCB SGS JSON API (``api.bcb.gov.br``).

Public surface:

- :class:`SgsDataClient` — context-manager client that wraps a
  ``quantilica.core.HttpClient`` and exposes the high-level
  :meth:`SgsDataClient.fetch_series_data` method, which returns
  :class:`~bcb_sgs_fetcher.models.SeriesPoint` instances.
- :func:`fetch_series_data` and :func:`get_daily_series` — module-level
  helpers that accept an existing ``HttpClient`` for callers that already
  manage their own session.
"""

import datetime as dt
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from types import TracebackType
from typing import Literal

import httpx
from quantilica.core.exceptions import FetchError
from quantilica.core.http import HttpClient

from . import logger
from .models import SeriesPoint

API_BASE_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs."

# Earliest year the retroactive daily walk will probe — a crash guard;
# the API normally signals the series start with a 404 long before this.
_MIN_DAILY_YEAR = 1900

Period = Literal["all", "latest"]


def get_url(series_id: int, period: Period = "all") -> str:
    """Build the URL for fetching a series.

    ``period="all"`` returns the full history. ``period="latest"``
    returns the last 20 observations.

    Args:
        series_id: BCB SGS series identifier.
        period: "all" or "latest".

    Returns:
        str: The URL for fetching the series data.
    """
    if period == "all":
        latest = ""
    elif period == "latest":
        latest = "/ultimos/20"
    else:
        raise ValueError(f"Invalid period {period!r}")
    return f"{API_BASE_URL}{series_id}/dados{latest}?formato=json"


def _get_json(
    url: str, client: HttpClient, progress: Callable[[int, int], None] | None = None
) -> list[dict[str, str | None]]:
    """GET ``url`` and return the parsed JSON, asserting it is a list."""
    logger.info("Requesting data from %s", url)
    data = client.get_json(url, progress=progress)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response format: {data}")
    return data


def _parse_point(raw: dict[str, str | None], series_id: int) -> SeriesPoint | None:
    """Convert a raw API record into a :class:`SeriesPoint`."""
    try:
        point_date = dt.datetime.strptime(raw["data"], "%d/%m/%Y").date()
        data_fim = raw.get("dataFim")
        point_date_end = (
            dt.datetime.strptime(data_fim, "%d/%m/%Y").date() if data_fim else None
        )
        valor = raw["valor"]
        point_value = Decimal(valor) if valor else None
    except (ValueError, TypeError, KeyError, InvalidOperation) as e:
        logger.warning(
            "Error parsing data for series %s: %s | raw=%s",
            series_id,
            e,
            raw,
        )
        return None
    return SeriesPoint(
        series_id=series_id,
        date=point_date,
        value=point_value,
        date_end=point_date_end,
    )


def get_daily_series(
    series_id: int,
    client: HttpClient,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[SeriesPoint]:
    """Fetch a daily series using the year-by-year retroactive strategy.

    The BCB SGS API limits how far back the unscoped ``/dados`` endpoint
    returns data for high-frequency series, so we:

    1. Pull the last 20 observations from ``/ultimos/20`` to anchor the
       most recent date.
    2. Walk backwards year by year requesting the full Jan 1 — Dec 31
       window, accumulating points until the series start is reached.

    The API signals "before the series start" with an HTTP 404 (raising
    :class:`~quantilica.core.exceptions.FetchError`); an unexpected
    payload raises :class:`ValueError`; both end the walk and the points
    collected so far are returned. ``_MIN_DAILY_YEAR`` is a crash guard.

    When *should_stop* returns ``True`` the backward walk halts, so a
    long backfill can be cancelled responsively (checked once per year).
    """
    raw_points: list[dict[str, str | None]] = []

    url = f"{API_BASE_URL}{series_id}/dados/ultimos/20?formato=json"
    latest = _get_json(url=url, client=client, progress=progress)
    if not latest:
        return []
    raw_points.extend(latest)

    last_date = dt.datetime.strptime(latest[0]["data"], "%d/%m/%Y").date()
    last_date -= dt.timedelta(days=1)

    year = last_date.year - 1
    start_date = dt.date(year, 1, 1)

    while year >= _MIN_DAILY_YEAR:
        if should_stop is not None and should_stop():
            break
        url = (
            f"{API_BASE_URL}{series_id}/dados?formato=json"
            f"&dataInicial={start_date:%d/%m/%Y}"
            f"&dataFinal={last_date:%d/%m/%Y}"
        )
        try:
            data_in_interval = _get_json(url=url, client=client, progress=progress)
        except (ValueError, FetchError):
            break
        if not data_in_interval:
            break
        raw_points.extend(data_in_interval)
        year -= 1
        start_date = dt.date(year, 1, 1)
        last_date = dt.date(year, 12, 31)

    parsed = (_parse_point(p, series_id) for p in raw_points)
    return [p for p in parsed if p is not None]


def fetch_series_data(
    series_id: int,
    client: HttpClient,
    period: Period = "all",
    frequency_acronym: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[SeriesPoint]:
    """Fetch the time series for ``series_id``.

    Args:
        series_id: BCB SGS series identifier.
        client: An ``HttpClient`` to use for the request.
        period: ``"all"`` (default) for the full history or ``"latest"``
            for the last 20 observations.
        frequency_acronym: One of ``"D"``, ``"S"``, ``"M"``, ``"T"``,
            ``"Qd"``, ``"A"``. Only ``"D"`` (daily) triggers the special
            retroactive strategy in :func:`get_daily_series`.
        should_stop: Optional predicate; when it returns ``True`` a daily
            backfill stops early (see :func:`get_daily_series`).
    """
    logger.info("Fetching data series %s", series_id)
    if frequency_acronym == "D" and period == "all":
        return get_daily_series(
            series_id=series_id,
            client=client,
            should_stop=should_stop,
            progress=progress,
        )

    url = get_url(series_id=series_id, period=period)
    try:
        raw = _get_json(url=url, client=client, progress=progress)
    except (ValueError, FetchError) as e:
        logger.warning("Error fetching data for series %s: %s", series_id, e)
        return []
    parsed = (_parse_point(p, series_id) for p in raw)
    return [p for p in parsed if p is not None]


class SgsDataClient:
    """High-level client for the BCB SGS JSON data API.

    Use as a context manager::

        with SgsDataClient() as client:
            points = client.fetch_series_data(series_id=1)
    """

    def __init__(
        self,
        timeout: float = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.client = HttpClient(timeout=timeout, transport=transport)

    def fetch_series_data(
        self,
        series_id: int,
        period: Period = "all",
        frequency_acronym: str | None = None,
        should_stop: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[SeriesPoint]:
        """Same as :func:`fetch_series_data` using the bound client."""
        return fetch_series_data(
            series_id=series_id,
            client=self.client,
            period=period,
            frequency_acronym=frequency_acronym,
            should_stop=should_stop,
            progress=progress,
        )

    def get_daily_series(
        self,
        series_id: int,
        should_stop: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[SeriesPoint]:
        """Same as :func:`get_daily_series` using the bound client."""
        return get_daily_series(
            series_id=series_id,
            client=self.client,
            should_stop=should_stop,
            progress=progress,
        )

    def close(self) -> None:
        pass  # HttpClient is stateless; connections close after each request

    def __enter__(self) -> "SgsDataClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
