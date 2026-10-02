"""One small interface to the AI, whichever provider is behind it.

    from app.services.llm import llm, AIUnavailable
    text = llm.ask("file_summaries", system, prompt, tier="fast", json=True)

Providers (picked with AI_PROVIDER / AI_FALLBACK, no code changes needed):
    gemini     Google Gemini API, free tier      (default)
    groq       Groq, free tier                   (fallback)
    anthropic  Anthropic Claude, optional, paid
    fake       canned answers, for tests and offline development

Free tiers have small limits, so every request goes through `ask`, which:
  1. answers from the cache if this exact request was answered before
     (results are stored in SQLite, so a project is never paid for twice);
  2. waits its turn: requests to one provider are sent one at a time and
     spaced out (AI_MIN_INTERVAL_SECONDS), like a polite queue;
  3. on "slow down" (HTTP 429/503) waits and retries a few times;
  4. counts requests per day and stops at AI_DAILY_LIMIT;
  5. tries the fallback provider when the main one is out;
  6. raises AIUnavailable with a friendly reason if nothing worked - the
     callers then show the non-AI version of the feature instead of an error.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Callable, Optional

import httpx

from .. import config, db


class AIUnavailable(Exception):
    """No AI answer right now. `reason` is one of:
    off (no provider configured), daily_limit, busy, error."""

    MESSAGES = {
        "off": "AI notes are switched off on this server, so you see what RepoSage found in the code itself.",
        "daily_limit": "The free AI service has reached today's limit. Everything that doesn't need AI still works; try the AI parts again tomorrow.",
        "busy": "The free AI service is very busy right now. Please try again in a minute.",
        "error": "The AI service didn't answer properly. Please try again in a minute.",
    }

    def __init__(self, reason: str) -> None:
        super().__init__(self.MESSAGES.get(reason, reason))
        self.reason = reason


class RateLimited(Exception):
    def __init__(self, retry_after: float | None = None, daily: bool = False) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after
        self.daily = daily


class ProviderError(Exception):
    pass


# ---------------------------------------------------------------------------
# Providers: each turns (system, prompt) into text over plain HTTPS.
# ---------------------------------------------------------------------------

class Provider:
    name = ""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=config.AI_TIMEOUT)

    @property
    def key(self) -> str:
        return config.api_key(self.name)

    def available(self) -> bool:
        return bool(self.key)

    def model(self, tier: str) -> str:
        return config.MODELS[self.name][tier]

    def send(self, system: str, prompt: str, model: str, max_tokens: int, json_mode: bool) -> str:
        raise NotImplementedError

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        try:
            resp = self.client.post(url, headers=headers, json=body)
        except httpx.HTTPError as e:
            raise ProviderError(f"network: {type(e).__name__}") from None
        if resp.status_code in (429, 503, 529):
            raise RateLimited(_retry_after(resp), daily=_looks_daily(resp.text))
        if resp.status_code >= 400:
            raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()


class GeminiProvider(Provider):
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def send(self, system, prompt, model, max_tokens, json_mode):
        gen: dict = {"maxOutputTokens": max_tokens, "temperature": 0.2}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        if "2.5-flash" in model:
            gen["thinkingConfig"] = {"thinkingBudget": 0}   # thinking would eat the free quota
        data = self._post(self.URL.format(model=model),
                          {"x-goog-api-key": self.key, "content-type": "application/json"},
                          {"systemInstruction": {"parts": [{"text": system}]},
                           "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": gen})
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError):
            raise ProviderError(f"unexpected answer: {str(data)[:300]}") from None


class GroqProvider(Provider):
    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def send(self, system, prompt, model, max_tokens, json_mode):
        body: dict = {"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": prompt}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._post(self.URL, {"authorization": f"Bearer {self.key}"}, body)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise ProviderError(f"unexpected answer: {str(data)[:300]}") from None


class AnthropicProvider(Provider):
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def send(self, system, prompt, model, max_tokens, json_mode):
        if json_mode:
            prompt += "\n\nReply with the JSON object only."
        data = self._post(self.URL, {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                          {"model": model, "max_tokens": max_tokens, "system": system,
                           "messages": [{"role": "user", "content": prompt}]})
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


class FakeProvider(Provider):
    """Answers instantly from handlers registered per request kind.
    Tests can also make it pretend to be rate limited."""
    name = "fake"
    handlers: dict[str, Callable[[str, str], str]] = {}
    fail_next: list[Exception] = []          # exceptions to raise, in order (tests)
    calls: list[str] = []                    # kinds asked, in order (tests)

    def available(self) -> bool:
        return True

    def send(self, system, prompt, model, max_tokens, json_mode):
        if self.fail_next:
            raise self.fail_next.pop(0)
        kind = re.search(r"\[kind:([\w-]+)\]", system)
        kind = kind.group(1) if kind else ""
        self.calls.append(kind)
        handler = self.handlers.get(kind)
        return handler(system, prompt) if handler else "This is a fake AI answer."


PROVIDERS = {"gemini": GeminiProvider, "groq": GroqProvider,
             "anthropic": AnthropicProvider, "fake": FakeProvider}


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _looks_daily(body: str) -> bool:
    """Gemini/Groq say which limit was hit; a per-day limit won't pass by waiting."""
    return bool(re.search(r"per ?day|PerDay|daily|RPD|TPD", body or "", re.I))


# ---------------------------------------------------------------------------
# The client everybody uses
# ---------------------------------------------------------------------------

class LLM:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._last_sent: dict[str, float] = {}
        self._out_today: dict[str, str] = {}     # provider -> day it ran out
        self._guard = threading.Lock()
        self.sleep = time.sleep                  # tests replace this to run fast

    # -- which providers to try --
    def chain(self) -> list[Provider]:
        names = [config.AI_PROVIDER] + ([config.AI_FALLBACK] if config.AI_FALLBACK != config.AI_PROVIDER else [])
        out = []
        for name in names:
            if name not in PROVIDERS or name in ("", "none", "off"):
                continue
            with self._guard:
                if name not in self._providers:
                    self._providers[name] = PROVIDERS[name]()
                    self._locks[name] = threading.Lock()
            if self._providers[name].available():
                out.append(self._providers[name])
        return out

    def status(self) -> dict:
        chain = self.chain()
        return {"enabled": bool(chain),
                "providers": [p.name for p in chain],
                "used_today": {p.name: db.usage_today(p.name) for p in chain},
                "daily_limit": config.AI_DAILY_LIMIT,
                "resting": bool(chain) and all(self._is_out(p.name) for p in chain)}

    # -- the one public call --
    def ask(self, kind: str, system: str, prompt: str, *, tier: str = "fast",
            json: bool = False, max_tokens: int = 1024,
            project_id: Optional[str] = None) -> str:
        system = f"{system}\n[kind:{kind}]"
        key = hashlib.sha256("\x1f".join([kind, tier, str(json), system, prompt]).encode()).hexdigest()
        cached = db.cache_get(key)
        if cached is not None:
            return cached

        chain = self.chain()
        if not chain:
            raise AIUnavailable("off")
        reason = "busy"
        for provider in chain:
            if self._is_out(provider.name):
                reason = "daily_limit"
                continue
            try:
                text = self._send_politely(provider, system, prompt, tier, max_tokens, json)
            except RateLimited as e:
                if e.daily:
                    self._out_today[provider.name] = _today()
                    reason = "daily_limit"
                continue
            except ProviderError:
                reason = "error"
                continue
            if json:
                try:
                    text = extract_json_text(text)
                except ValueError:
                    reason = "error"         # not JSON: never cache it, try the next one
                    continue
            db.cache_put(key, project_id, kind, provider.name, provider.model(tier), text)
            return text
        raise AIUnavailable(reason)

    def _send_politely(self, p: Provider, system, prompt, tier, max_tokens, json_mode) -> str:
        """One request at a time per provider, spaced out, with retries."""
        with self._locks[p.name]:
            for attempt in range(config.AI_MAX_RETRIES + 1):
                if db.usage_today(p.name) >= config.AI_DAILY_LIMIT:
                    raise RateLimited(daily=True)
                wait = self._last_sent.get(p.name, 0) + config.AI_MIN_INTERVAL - time.monotonic()
                if wait > 0 and p.name != "fake":
                    self.sleep(wait)
                self._last_sent[p.name] = time.monotonic()
                db.count_request(p.name)
                try:
                    return p.send(system, prompt, p.model(tier), max_tokens, json_mode)
                except RateLimited as e:
                    if e.daily or attempt == config.AI_MAX_RETRIES:
                        raise
                    # wait as long as the provider asks, else 2x, 4x, 8x the spacing
                    self.sleep(min(60.0, e.retry_after or config.AI_MIN_INTERVAL * 2 ** (attempt + 1)))
            raise RateLimited()

    def _is_out(self, name: str) -> bool:
        return self._out_today.get(name) == _today() or db.usage_today(name) >= config.AI_DAILY_LIMIT

    def reset(self) -> None:
        """Forget providers and limits (used by tests after changing config)."""
        self.__init__()


def extract_json_text(text: str) -> str:
    """Models sometimes wrap JSON in ```json fences or add a sentence."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start > 0:
        text = text[start:]
    json.loads(text)        # raises ValueError if it still isn't JSON
    return text


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


llm = LLM()
