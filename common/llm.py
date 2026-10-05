"""The only place that talks to the LLM.

Works with any OpenAI-compatible provider (Gemini, Groq, Ollama, ...); switch
providers by changing LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in .env.
"""
import json
import logging
import re
from functools import lru_cache

from openai import (APIConnectionError, APITimeoutError, BadRequestError, InternalServerError,
                    OpenAI, RateLimitError)
from tenacity import (before_sleep_log, retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from common.config import settings

log = logging.getLogger(__name__)

# Temporary problems worth waiting out. Anything else (bad key, wrong model) fails fast.
RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


class LLMError(Exception):
    """The model answered, but not with usable JSON."""


@lru_cache
def get_client() -> OpenAI:
    # max_retries=0: tenacity below handles retries, with longer waits suited to free tiers
    return OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                  timeout=settings.llm_timeout, max_retries=0)


@retry(retry=retry_if_exception_type(RETRYABLE),
       wait=wait_exponential(multiplier=2, min=5, max=60),
       stop=stop_after_attempt(5),
       before_sleep=before_sleep_log(log, logging.WARNING),
       reraise=True)
def _chat(messages: list[dict], max_tokens: int, json_mode: bool):
    kwargs = {"model": settings.llm_model, "messages": messages, "max_tokens": max_tokens}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if settings.llm_reasoning_effort:
        kwargs["reasoning_effort"] = settings.llm_reasoning_effort
    return get_client().chat.completions.create(**kwargs)


def parse_json(text: str):
    """Parse JSON even if wrapped in ```json fences or surrounded by stray text."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def complete_json(system: str, user: str, max_tokens: int = 8000):
    """Send a prompt and return the parsed JSON reply.

    max_tokens must leave room for the model's hidden thinking AND the answer.
    """
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        resp = _chat(messages, max_tokens, settings.llm_json_mode)
    except BadRequestError:
        if not settings.llm_json_mode:
            raise
        # Some providers/models reject response_format; retry once without it.
        log.warning("Provider rejected JSON mode; retrying without it")
        resp = _chat(messages, max_tokens, False)

    choice = resp.choices[0]
    text = choice.message.content or ""
    if not text.strip():
        raise LLMError(f"empty reply (finish_reason={choice.finish_reason}); "
                       "try a larger --max-tokens or smaller --batch-size")
    try:
        return parse_json(text)
    except (json.JSONDecodeError, ValueError) as e:
        hint = " (reply was cut off: raise --max-tokens)" if choice.finish_reason == "length" else ""
        raise LLMError(f"invalid JSON{hint}: {e}; reply starts: {text[:150]!r}") from e
