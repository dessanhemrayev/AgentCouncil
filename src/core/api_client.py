"""Opt-in HTTP delivery for API members (T7): OpenAI Responses API, stdlib only.

One endpoint for v1: POST {base_url}/v1/responses. No `openai` package —
urllib.request + json keep the zero-runtime-dependency invariant. Auth is a
Bearer token read from os.environ[api_key_env] (the key itself never lands
in council.json). One retry on 429/5xx per the plan; everything that
survives it becomes ApiError / ApiTimeout, which the runner
(openai_runner.py) converts to AgentResult(error=...) so a failed API
member drops out of the round instead of crashing it (same policy as
CliRunner).
"""

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.openai.com"

# Same scale as the CLI agent timeouts (RunContext.timeout defaults to 600).
REQUEST_TIMEOUT = 600.0

# Delay before the single retry on 429/5xx.
RETRY_DELAY_SECONDS = 2.0

# API error bodies can be long HTML/JSON dumps — truncate in messages.
_MAX_ERROR_BODY_CHARS = 500


class ApiError(Exception):
    """A delivery failure that survived the built-in retry."""


class ApiTimeout(ApiError):
    """The request exceeded its read timeout."""


def _extract_output_text(payload: dict) -> str:
    """Assistant text from a /v1/responses payload.

    The SDK's `output_text` convenience does not exist in the raw REST
    response: text lives in output[] items of type "message", inside
    content[] items of type "output_text". Everything else (reasoning
    items, tool calls) contributes nothing.
    """
    parts: list[str] = []
    output = payload.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        for piece in content if isinstance(content, list) else []:
            if isinstance(piece, dict) and piece.get("type") == "output_text":
                text = piece.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "\n".join(parts)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """Truncated response body of a failed HTTP call (for messages)."""
    try:
        detail = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001 — error body is best-effort only
        return str(exc.reason)
    if not detail:
        return str(exc.reason)
    if len(detail) > _MAX_ERROR_BODY_CHARS:
        detail = detail[:_MAX_ERROR_BODY_CHARS] + "…"
    return detail


def responses_post(
    *,
    base_url: str,
    model: str,
    input_text: str,
    api_key_env: str,
    timeout: float = REQUEST_TIMEOUT,
) -> str:
    """POST one prompt to the Responses API; return the assistant text.

    Raises:
    - ApiError("NO KEY: ...") — the env var named by api_key_env is missing
      or empty (checked before any network activity);
    - ApiTimeout — the response did not arrive within `timeout`;
    - ApiError — any other HTTP/network failure after the single retry
      (429 and 5xx are retried once; 4xx are not — a bad key does not get
      better by trying again).
    """
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ApiError(
            f"NO KEY: environment variable {api_key_env} is missing or empty."
        )

    url = base_url.rstrip("/") + "/v1/responses"
    body = json.dumps({"model": model, "input": input_text}).encode("utf-8")

    for attempt in (1, 2):
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return _extract_output_text(payload)
        except urllib.error.HTTPError as exc:
            message = f"HTTP {exc.code}: {_error_detail(exc)}"
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt == 1:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
            raise ApiError(f"{model}: {message}") from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise ApiTimeout(
                    f"Timeout: API did not respond within {timeout} sec."
                ) from exc
            raise ApiError(f"{model}: URLError: {getattr(exc, 'reason', exc)}") from exc
        except TimeoutError:
            raise ApiTimeout(
                f"Timeout: API did not respond within {timeout} sec."
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(f"{model}: invalid API response: {exc}") from exc

    raise ApiError(
        f"{model}: request attempts exhausted"
    )  # pragma: no cover — loop guard
