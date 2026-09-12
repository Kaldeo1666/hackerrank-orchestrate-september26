"""
LLM provider abstraction for "Buy or Wait?".

Switch providers with ONE environment variable — no code changes needed
anywhere else in the pipeline. Every call site (image extraction now,
decision_explanation generation in Block 3) goes through call_vision()
or call_text() below, never touches a provider SDK directly.

.env settings:
    LLM_PROVIDER=gemini        # gemini | anthropic | openai   (default: gemini)
    LLM_MODEL=gemini-2.0-flash # optional override, sensible default per provider
    GEMINI_API_KEY=...         # required if LLM_PROVIDER=gemini
    ANTHROPIC_API_KEY=...      # required if LLM_PROVIDER=anthropic
    OPENAI_API_KEY=...         # required if LLM_PROVIDER=openai

Default is Gemini because Google AI Studio currently offers a genuinely
free tier (no card required) that comfortably covers this dataset's
volume (~20 images, 250 requests). See PROJECT_STATE.md decisions log
for the full reasoning.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass


@dataclass
class LLMResult:
    text: str                 # raw text response (before JSON parsing)
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```")
    return text.strip()


def parse_json_response(result: LLMResult, fallback: dict) -> dict:
    """Best-effort JSON parse of a model's text response. Returns `fallback`
    (annotated with the raw text) if parsing fails, so callers never crash
    on a malformed response — they just get a clearly-flagged failure.

    Models often ignore "respond with ONLY JSON" and add a sentence before
    or after the object anyway — so after a direct parse attempt, we fall
    back to pulling out the first {...} block found anywhere in the text."""
    cleaned = _strip_code_fence(result.text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    out = dict(fallback)
    out["_parse_error"] = result.text[:300]
    return out


class LLMClient:
    """Unified interface. Construct via get_client() below, not directly."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model

    def call_vision(self, system_prompt: str, user_prompt: str, image_bytes: bytes,
                     image_mime: str = "image/png", max_tokens: int = 300) -> LLMResult:
        raise NotImplementedError

    def call_text(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> LLMResult:
        raise NotImplementedError


class GeminiClient(LLMClient):
    def __init__(self, model: str):
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        self._genai = genai
        super().__init__("gemini", model)

    def call_vision(self, system_prompt, user_prompt, image_bytes, image_mime="image/png", max_tokens=300):
        model = self._genai.GenerativeModel(self.model, system_instruction=system_prompt)
        image_part = {"mime_type": image_mime, "data": image_bytes}
        response = model.generate_content(
            [image_part, user_prompt],
            generation_config={"max_output_tokens": max_tokens},
        )
        usage = getattr(response, "usage_metadata", None)
        return LLMResult(
            text=response.text,
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
            model=self.model, provider="gemini",
        )

    def call_text(self, system_prompt, user_prompt, max_tokens=500):
        model = self._genai.GenerativeModel(self.model, system_instruction=system_prompt)
        response = model.generate_content(user_prompt, generation_config={"max_output_tokens": max_tokens})
        usage = getattr(response, "usage_metadata", None)
        return LLMResult(
            text=response.text,
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
            model=self.model, provider="gemini",
        )


class AnthropicClient(LLMClient):
    def __init__(self, model: str):
        import anthropic
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        super().__init__("anthropic", model)

    def call_vision(self, system_prompt, user_prompt, image_bytes, image_mime="image/png", max_tokens=300):
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        response = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system_prompt,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": image_mime, "data": b64}},
                {"type": "text", "text": user_prompt},
            ]}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return LLMResult(text=text, input_tokens=response.usage.input_tokens,
                          output_tokens=response.usage.output_tokens, model=self.model, provider="anthropic")

    def call_text(self, system_prompt, user_prompt, max_tokens=500):
        response = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return LLMResult(text=text, input_tokens=response.usage.input_tokens,
                          output_tokens=response.usage.output_tokens, model=self.model, provider="anthropic")


class OpenAIClient(LLMClient):
    def __init__(self, model: str):
        import openai
        self._client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        super().__init__("openai", model)

    def call_vision(self, system_prompt, user_prompt, image_bytes, image_mime="image/png", max_tokens=300):
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{image_mime};base64,{b64}"
        response = self._client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
        )
        text = response.choices[0].message.content
        usage = response.usage
        return LLMResult(text=text, input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                          model=self.model, provider="openai")

    def call_text(self, system_prompt, user_prompt, max_tokens=500):
        response = self._client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        )
        text = response.choices[0].message.content
        usage = response.usage
        return LLMResult(text=text, input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                          model=self.model, provider="openai")


_DEFAULT_MODELS = {
    "gemini": "gemini-flash-lite-latest",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}

_RATE_LIMIT_KEYWORDS = ("429", "resource_exhausted", "rate limit", "quota", "too many requests")


def call_with_retry(fn, max_attempts: int = 6, default_wait: float = 20.0):
    """Call fn() and retry on rate-limit errors, waiting between attempts.
    Tries to read the provider's suggested retry delay out of the error
    message (Gemini includes "Please retry in 42.4s" style text); falls
    back to `default_wait * attempt_number` if it can't find one.

    Non-rate-limit errors are raised immediately — we only retry the
    specific "you're going too fast" case, not real bugs.
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            is_rate_limit = any(kw in msg for kw in _RATE_LIMIT_KEYWORDS)
            if not is_rate_limit:
                raise
            match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(e), re.IGNORECASE)
            wait = float(match.group(1)) + 2 if match else default_wait * attempt
            print(f"  Rate limited (attempt {attempt}/{max_attempts}) — waiting {wait:.0f}s before retry...")
            time.sleep(wait)
    raise last_error


def get_client() -> LLMClient:
    """Reads LLM_PROVIDER (default 'gemini') and LLM_MODEL from the
    environment and returns a ready-to-use client. This is the ONLY
    place provider selection happens — call this, never a provider
    class directly, so swapping providers never touches call sites."""
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
    model = os.environ.get("LLM_MODEL") or _DEFAULT_MODELS[provider]

    if provider == "gemini":
        return GeminiClient(model)
    if provider == "anthropic":
        return AnthropicClient(model)
    if provider == "openai":
        return OpenAIClient(model)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}. Use gemini, anthropic, or openai.")