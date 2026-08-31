"""Tests for the JSON data fetcher (api.bcb.gov.br)."""

import datetime as dt
import json
from decimal import Decimal

import httpx2
import pytest
from quantilica.core.http import HttpClient

from bcb_sgs_fetcher import (
    SeriesPoint,
    SgsDataClient,
    fetch_series_data,
    get_url,
)


def test_get_url_all():
    assert get_url(1, "all") == (
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados?formato=json"
    )


def test_get_url_latest():
    assert get_url(4189, "latest") == (
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4189"
        "/dados/ultimos/20?formato=json"
    )


def test_get_url_invalid_period():
    with pytest.raises(ValueError):
        get_url(1, "bogus")  # type: ignore[arg-type]


def _handler_json(payload):
    """Build an httpx2.MockTransport handler returning ``payload`` as JSON."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    return handler


def test_fetch_monthly_series_returns_points():
    payload = [
        {"data": "01/01/2024", "valor": "10.5"},
        {"data": "01/02/2024", "valor": "11.0"},
        {"data": "01/03/2024", "valor": ""},  # null value
    ]
    transport = httpx2.MockTransport(_handler_json(payload))
    client = HttpClient(transport=transport)
    points = fetch_series_data(
        series_id=4189, client=client, period="all", frequency_acronym="M"
    )

    assert points == [
        SeriesPoint(
            series_id=4189,
            date=dt.date(2024, 1, 1),
            value=Decimal("10.5"),
        ),
        SeriesPoint(
            series_id=4189,
            date=dt.date(2024, 2, 1),
            value=Decimal("11.0"),
        ),
        SeriesPoint(
            series_id=4189,
            date=dt.date(2024, 3, 1),
            value=None,
        ),
    ]


def test_fetch_period_with_data_fim():
    payload = [
        {
            "data": "01/01/2024",
            "dataFim": "31/01/2024",
            "valor": "1.2345",
        },
    ]
    transport = httpx2.MockTransport(_handler_json(payload))
    client = HttpClient(transport=transport)
    points = fetch_series_data(series_id=99, client=client, period="latest")

    assert points == [
        SeriesPoint(
            series_id=99,
            date=dt.date(2024, 1, 1),
            value=Decimal("1.2345"),
            date_end=dt.date(2024, 1, 31),
        )
    ]


def test_unexpected_payload_logs_and_returns_empty():
    # ``get_json`` returns an error JSON object — fetch_series_data must
    # swallow the ValueError raised internally and return an empty list.
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=json.dumps({"error": "Series not found"}).encode(),
            headers={"Content-Type": "application/json"},
        )

    transport = httpx2.MockTransport(handler)
    client = HttpClient(transport=transport)
    assert fetch_series_data(series_id=1, client=client, period="all") == []


def test_daily_series_walks_years_backwards():
    """Daily fetch starts with ``/ultimos/20`` then walks back year-by-year.

    The mock answers ``/ultimos/20`` with two points; the first
    historical window returns one extra point; the next returns an
    empty-but-invalid payload (dict) and stops the loop.
    """
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        calls.append(url)
        if "/ultimos/20" in url:
            payload = [
                {"data": "05/03/2024", "valor": "5.5"},
                {"data": "04/03/2024", "valor": "5.4"},
            ]
        elif "dataInicial=01/01/2023" in url:
            payload = [{"data": "15/06/2023", "valor": "5.0"}]
        else:
            # Anything earlier than 2023 → API returns malformed payload
            # (a dict instead of a list), which `_get_json` rejects with
            # `ValueError` and the loop terminates.
            return httpx2.Response(
                200,
                content=json.dumps({"error": "no data"}).encode(),
                headers={"Content-Type": "application/json"},
            )
        return httpx2.Response(
            200,
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    transport = httpx2.MockTransport(handler)
    with SgsDataClient(transport=transport) as client:
        points = client.fetch_series_data(
            series_id=1, period="all", frequency_acronym="D"
        )

    dates = [p.date for p in points]
    assert dt.date(2024, 3, 5) in dates
    assert dt.date(2024, 3, 4) in dates
    assert dt.date(2023, 6, 15) in dates
    # The first /ultimos/20 must be hit, plus at least one window.
    assert any("ultimos/20" in c for c in calls)
    assert any("dataInicial=01/01/2023" in c for c in calls)


def test_daily_series_stops_on_404():
    """A 404 (before series start) ends the walk; data so far is kept."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if "/ultimos/20" in url:
            payload = [
                {"data": "05/03/2024", "valor": "5.5"},
                {"data": "04/03/2024", "valor": "5.4"},
            ]
        elif "dataInicial=01/01/2023" in url:
            payload = [{"data": "15/06/2023", "valor": "5.0"}]
        else:
            # The real BCB API returns 404 for years before the start.
            return httpx2.Response(404, content=b"not found")
        return httpx2.Response(
            200,
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    transport = httpx2.MockTransport(handler)
    with SgsDataClient(transport=transport) as client:
        points = client.fetch_series_data(
            series_id=1, period="all", frequency_acronym="D"
        )

    dates = {p.date for p in points}
    assert {
        dt.date(2024, 3, 5),
        dt.date(2024, 3, 4),
        dt.date(2023, 6, 15),
    } <= dates


def test_non_daily_404_returns_empty():
    """A 404 on the plain ``/dados`` endpoint yields no points (not error)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, content=b"not found")

    transport = httpx2.MockTransport(handler)
    with SgsDataClient(transport=transport) as client:
        assert client.fetch_series_data(series_id=42, period="all") == []


def test_daily_series_stops_when_should_stop():
    """``should_stop`` halts the year-by-year walk after the anchor call."""
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        payload = [
            {"data": "05/03/2024", "valor": "5.5"},
            {"data": "04/03/2024", "valor": "5.4"},
        ]
        return httpx2.Response(
            200,
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    transport = httpx2.MockTransport(handler)
    with SgsDataClient(transport=transport) as client:
        points = client.fetch_series_data(
            series_id=1,
            period="all",
            frequency_acronym="D",
            should_stop=lambda: True,
        )

    assert len(points) == 2
    assert len(calls) == 1  # only /ultimos/20, no year windows


def test_invalid_date_in_record_is_skipped():
    payload = [
        {"data": "not-a-date", "valor": "1"},
        {"data": "01/01/2024", "valor": "2"},
    ]
    transport = httpx2.MockTransport(_handler_json(payload))
    with SgsDataClient(transport=transport) as client:
        points = client.fetch_series_data(series_id=1, period="latest")
    assert points == [
        SeriesPoint(series_id=1, date=dt.date(2024, 1, 1), value=Decimal("2"))
    ]
