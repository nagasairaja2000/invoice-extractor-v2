"""Gemini integration with structured output, retries and model fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.config import Settings
from app.core.exceptions import ExtractionError
from app.prompts.invoice_extraction import SYSTEM_PROMPT, USER_PROMPT
from app.schemas.invoice import INVOICE_JSON_SCHEMA, InvoiceData
from app.utils import json_tools

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_MODEL_UNAVAILABLE_STATUS = {403, 404}


@dataclass(slots=True)
class GeminiExtraction:
    invoice: InvoiceData
    model_used: str
    tokens: dict[str, int] = field(default_factory=dict)
    raw_json: str = ""


def _error_status(exc: Exception) -> int | None:
    """Best-effort HTTP status extraction from SDK exceptions."""
    for attribute in ("status_code", "code", "status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    message = str(exc).upper()
    token_map = (
        ("RESOURCE_EXHAUSTED", 429),
        ("NOT_FOUND", 404),
        ("PERMISSION_DENIED", 403),
        ("INVALID_ARGUMENT", 400),
        ("UNAVAILABLE", 503),
        ("DEADLINE_EXCEEDED", 504),
        ("INTERNAL", 500),
    )
    for token, status in token_map:
        if token in message:
            return status

    match = re.search(r"\b(400|403|404|408|429|500|502|503|504)\b", message)
    return int(match.group(1)) if match else None


class GeminiService:
    def __init__(self, settings: Settings, client: genai.Client | None = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            if not self.settings.gemini_api_key:
                raise ExtractionError(
                    "GEMINI_API_KEY is not configured. Add it to your .env file.",
                    code="GEMINI_NOT_CONFIGURED",
                    status_code=503,
                )
            self._client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(
                    timeout=self.settings.gemini_timeout_seconds * 1000
                ),
            )
        return self._client

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------
    def _build_contents(self, text: str | None, images: list[bytes] | None) -> list:
        contents: list = [USER_PROMPT]

        if text:
            contents.append(f"\n\nINVOICE TEXT:\n---\n{text}\n---")

        if images:
            for index, png in enumerate(images, start=1):
                contents.append(f"\nInvoice page {index}:")
                contents.append(types.Part.from_bytes(data=png, mime_type="image/png"))

        return contents

    def _config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=INVOICE_JSON_SCHEMA,
            temperature=0.1,
            max_output_tokens=self.settings.gemini_max_output_tokens,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def extract(
        self,
        *,
        text: str | None = None,
        images: list[bytes] | None = None,
        filename: str = "invoice.pdf",
    ) -> GeminiExtraction:
        if not text and not images:
            raise ExtractionError(
                "No invoice content was available for extraction.",
                code="NO_CONTENT",
            )

        contents = self._build_contents(text, images)
        config = self._config()
        retries = max(self.settings.gemini_max_retries, 0)
        last_error: Exception | None = None

        for model in self.settings.gemini_models:
            for attempt in range(retries + 1):
                try:
                    response = await asyncio.wait_for(
                        self.client.aio.models.generate_content(
                            model=model,
                            contents=contents,
                            config=config,
                        ),
                        timeout=self.settings.gemini_timeout_seconds,
                    )
                    return self._parse(response, model)

                except asyncio.TimeoutError as exc:
                    last_error = exc
                    logger.warning(
                        "Gemini timeout for model %s (attempt %s/%s)",
                        model,
                        attempt + 1,
                        retries + 1,
                    )
                    if attempt < retries:
                        await asyncio.sleep(self._backoff(attempt))
                        continue
                    break

                except ExtractionError:
                    # Parsing/validation failures already carry a precise code.
                    # Never reclassify or retry them as transport errors.
                    raise

                except Exception as exc:  # noqa: BLE001 - classify SDK errors
                    status = _error_status(exc)
                    last_error = exc

                    if status in _RETRYABLE_STATUS:
                        logger.warning(
                            "Gemini transient error %s for %s (attempt %s/%s)",
                            status,
                            model,
                            attempt + 1,
                            retries + 1,
                        )
                        if attempt < retries:
                            await asyncio.sleep(self._backoff(attempt))
                            continue
                        break

                    if status in _MODEL_UNAVAILABLE_STATUS:
                        logger.warning(
                            "Model %s unavailable (status %s); trying next model.", model, status
                        )
                        break

                    if status == 400:
                        raise ExtractionError(
                            "Gemini rejected the extraction request.",
                            code="GEMINI_INVALID_REQUEST",
                            details=str(exc)[:800],
                        ) from exc

                    logger.exception("Unexpected Gemini error for model %s", model)
                    raise ExtractionError(
                        "Gemini request failed.",
                        code="GEMINI_REQUEST_FAILED",
                        details=str(exc)[:800],
                    ) from exc

        raise ExtractionError(
            "Invoice extraction failed: no Gemini model produced a response.",
            code="GEMINI_ALL_MODELS_FAILED",
            details=str(last_error)[:800] if last_error else None,
        )

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2 ** attempt + random.uniform(0, 0.4), 8.0)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------
    def _parse(self, response, model: str) -> GeminiExtraction:
        parsed = getattr(response, "parsed", None)
        raw_text = getattr(response, "text", None) or ""

        if isinstance(parsed, InvoiceData):
            invoice = parsed
            raw_json = raw_text or parsed.model_dump_json()
        else:
            data = parsed if isinstance(parsed, dict) else None

            if data is None:
                if not raw_text.strip():
                    raise ExtractionError(
                        "Gemini returned an empty response.",
                        code="GEMINI_EMPTY_RESPONSE",
                    )
                try:
                    data = json_tools.loads(raw_text)
                except ValueError as exc:
                    raise ExtractionError(
                        "Gemini returned invalid JSON.",
                        code="GEMINI_INVALID_JSON",
                        details=str(exc)[:500],
                    ) from exc

            if not isinstance(data, dict):
                raise ExtractionError(
                    "Gemini returned JSON that is not an object.",
                    code="GEMINI_INVALID_JSON",
                )

            try:
                invoice = InvoiceData.model_validate(data)
            except ValidationError as exc:
                raise ExtractionError(
                    "Gemini output did not match the invoice schema.",
                    code="GEMINI_SCHEMA_MISMATCH",
                    details=str(exc)[:1000],
                ) from exc

            raw_json = raw_text or json.dumps(data, ensure_ascii=False)

        usage = getattr(response, "usage_metadata", None)
        tokens: dict[str, int] = {}
        if usage is not None:
            for key, attribute in (
                ("prompt", "prompt_token_count"),
                ("completion", "candidates_token_count"),
                ("total", "total_token_count"),
            ):
                value = getattr(usage, attribute, None)
                if isinstance(value, int):
                    tokens[key] = value

        return GeminiExtraction(
            invoice=invoice,
            model_used=model,
            tokens=tokens,
            raw_json=raw_json,
        )
