"""Tests for Gemini request building, structured parsing, retries and fallback."""

from __future__ import annotations

import asyncio
from typing import Any

from types import SimpleNamespace

import pytest
from google.genai import types

from app.config import Settings
from app.core.exceptions import ExtractionError
from app.schemas.invoice import InvoiceData
from app.services.gemini_service import GeminiService
from conftest import build_invoice


class FakeApiError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class FakeUsage:
    prompt_token_count = 100
    candidates_token_count = 50
    total_token_count = 150


class FakeResponse:
    def __init__(self, parsed=None, text: str | None = None):
        self.parsed = parsed
        self.text = text
        self.usage_metadata = FakeUsage()


class FakeModels:
    def __init__(self, script: dict[str, Any]):
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        value = self.script[model]
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, Exception):
            raise value
        if asyncio.iscoroutine(value):
            return await value
        return value


class FakeClient:
    def __init__(self, script: dict[str, Any]):
        self.models = FakeModels(script)
        self.aio = SimpleNamespace(models=self.models)


def make_service(settings: Settings, script: dict[str, Any]) -> tuple[GeminiService, FakeClient]:
    client = FakeClient(script)
    return GeminiService(settings, client=client), client


def with_settings(settings: Settings, **overrides) -> Settings:
    return settings.model_copy(update=overrides)


@pytest.mark.asyncio
async def test_extract_parses_structured_dict(settings):
    invoice = build_invoice()
    service, client = make_service(
        settings, {"gemini-test": FakeResponse(parsed=invoice.model_dump(mode="json"))}
    )

    result = await service.extract(text="invoice text", filename="a.pdf")

    assert result.invoice.invoice_number == "TEST-2026-001"
    assert result.invoice.total_amount == invoice.total_amount
    assert result.model_used == "gemini-test"
    assert result.tokens == {"prompt": 100, "completion": 50, "total": 150}
    assert "TEST-2026-001" in result.raw_json
    assert client.models.calls[0]["model"] == "gemini-test"


@pytest.mark.asyncio
async def test_extract_parses_json_text_when_parsed_is_missing(settings):
    payload = '{"invoice_number": "INV-9", "total_amount": "42.00"}'
    service, _ = make_service(settings, {"gemini-test": FakeResponse(parsed=None, text=payload)})

    result = await service.extract(text="invoice text")

    assert result.invoice.invoice_number == "INV-9"
    assert result.raw_json == payload


@pytest.mark.asyncio
async def test_extract_accepts_invoice_instance(settings):
    invoice = build_invoice()
    service, _ = make_service(settings, {"gemini-test": FakeResponse(parsed=invoice)})

    result = await service.extract(text="invoice text")

    assert result.invoice is invoice
    assert "TEST-2026-001" in result.raw_json


@pytest.mark.asyncio
async def test_extract_builds_multimodal_contents(settings):
    invoice = build_invoice()
    service, client = make_service(
        settings, {"gemini-test": FakeResponse(parsed=invoice.model_dump(mode="json"))}
    )

    await service.extract(text="plain text", images=[b"\x89PNG-fake"], filename="scan.pdf")

    contents = client.models.calls[0]["contents"]
    assert any("plain text" in item for item in contents if isinstance(item, str))
    assert any(isinstance(item, types.Part) for item in contents)
    config = client.models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.temperature == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_extract_requires_content(settings):
    service, _ = make_service(settings, {})

    with pytest.raises(ExtractionError) as excinfo:
        await service.extract()

    assert excinfo.value.code == "NO_CONTENT"


@pytest.mark.asyncio
async def test_extract_rejects_empty_response(settings):
    service, _ = make_service(settings, {"gemini-test": FakeResponse(parsed=None, text="  ")})

    with pytest.raises(ExtractionError) as excinfo:
        await service.extract(text="invoice text")

    assert excinfo.value.code == "GEMINI_EMPTY_RESPONSE"


@pytest.mark.asyncio
async def test_extract_rejects_invalid_json(settings):
    service, _ = make_service(settings, {"gemini-test": FakeResponse(parsed=None, text="{nope")})

    with pytest.raises(ExtractionError) as excinfo:
        await service.extract(text="invoice text")

    assert excinfo.value.code == "GEMINI_INVALID_JSON"


@pytest.mark.asyncio
async def test_extract_rejects_json_that_is_not_an_object(settings):
    service, _ = make_service(settings, {"gemini-test": FakeResponse(parsed=None, text="[]")})

    with pytest.raises(ExtractionError) as excinfo:
        await service.extract(text="invoice text")

    assert excinfo.value.code == "GEMINI_INVALID_JSON"


@pytest.mark.asyncio
async def test_extract_reports_schema_mismatch(settings):
    service, _ = make_service(
        settings, {"gemini-test": FakeResponse(parsed={"line_items": [5]})}
    )

    with pytest.raises(ExtractionError) as excinfo:
        await service.extract(text="invoice text")

    assert excinfo.value.code == "GEMINI_SCHEMA_MISMATCH"


@pytest.mark.asyncio
async def test_extract_falls_back_when_model_is_unavailable(settings):
    settings = with_settings(
        settings, gemini_model="primary", gemini_fallback_models_raw="secondary"
    )
    invoice = build_invoice()
    service, client = make_service(
        settings,
        {
            "primary": FakeApiError("model is no longer available", status_code=404),
            "secondary": FakeResponse(parsed=invoice.model_dump(mode="json")),
        },
    )

    result = await service.extract(text="invoice text")

    assert result.model_used == "secondary"
    assert [call["model"] for call in client.models.calls] == ["primary", "secondary"]


@pytest.mark.asyncio
async def test_extract_retries_transient_errors(settings, monkeypatch):
    settings = with_settings(settings, gemini_max_retries=1)
    invoice = build_invoice()
    service, client = make_service(
        settings,
        {
            "gemini-test": [
                FakeApiError("high demand", status_code=503),
                FakeResponse(parsed=invoice.model_dump(mode="json")),
            ]
        },
    )
    monkeypatch.setattr(GeminiService, "_backoff", staticmethod(lambda _attempt: 0))

    result = await service.extract(text="invoice text")

    assert result.model_used == "gemini-test"
    assert len(client.models.calls) == 2


@pytest.mark.asyncio
async def test_extract_fails_after_all_models_are_exhausted(settings, monkeypatch):
    settings = with_settings(settings, gemini_max_retries=0)
    service, _ = make_service(
        settings, {"gemini-test": FakeApiError("high demand", status_code=503)}
    )
    monkeypatch.setattr(GeminiService, "_backoff", staticmethod(lambda _attempt: 0))

    with pytest.raises(ExtractionError) as excinfo:
        await service.extract(text="invoice text")

    assert excinfo.value.code == "GEMINI_ALL_MODELS_FAILED"
    assert "high demand" in (excinfo.value.details or "")


@pytest.mark.asyncio
async def test_extract_turns_bad_request_into_invalid_request_error(settings):
    service, _ = make_service(
        settings, {"gemini-test": FakeApiError("bad payload", status_code=400)}
    )

    with pytest.raises(ExtractionError) as excinfo:
        await service.extract(text="invoice text")

    assert excinfo.value.code == "GEMINI_INVALID_REQUEST"


@pytest.mark.asyncio
async def test_extract_falls_back_after_request_timeout(settings):
    async def slow_response():
        await asyncio.sleep(2)
        return FakeResponse(parsed=build_invoice().model_dump(mode="json"))

    settings = with_settings(settings, gemini_timeout_seconds=1, gemini_max_retries=0)
    service, _ = make_service(settings, {"gemini-test": slow_response()})

    with pytest.raises(ExtractionError) as excinfo:
        await service.extract(text="invoice text")

    assert excinfo.value.code == "GEMINI_ALL_MODELS_FAILED"


def test_client_requires_api_key(settings):
    settings = with_settings(settings, gemini_api_key=None)
    service = GeminiService(settings)

    with pytest.raises(ExtractionError) as excinfo:
        _ = service.client

    assert excinfo.value.code == "GEMINI_NOT_CONFIGURED"


def test_error_status_extraction():
    from app.services.gemini_service import _error_status

    assert _error_status(FakeApiError("x", 429)) == 429
    assert _error_status(Exception("RESOURCE_EXHAUSTED: quota")) == 429
    assert _error_status(Exception("resource exhausted (code 429)")) == 429
    assert _error_status(Exception("something weird")) is None
    assert _error_status(Exception("status_code=503")) == 503
