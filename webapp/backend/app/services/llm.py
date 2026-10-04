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
import logging
import os
import re
import threading
import time
from typing import Callable, Optional

import httpx

from .. import config, db


log = logging.getLogger("reposage.ai")


class AIUnavailable(Exception):
    """No AI answer right now. `reason` is what the student is told:
        off          no provider configured (no key)
        setup        a key/model/endpoint problem the site owner must fix
        daily_limit  today's free quota is used up
        busy         rate limited, overloaded or unreachable: try again soon
        error        the AI answered, but the reply couldn't be used
    `detail` is the technical reason, for the server log only."""

    MESSAGES = {
        "off": "AI isn't set up on this server, so RepoSage answers from the code itself.",
        "setup": "The AI service isn't set up correctly on this server right now, so RepoSage answers from "
                 "the code itself. (The site owner can see the reason in the server log.)",
        "daily_limit": "The free AI service has reached today's limit. Everything that doesn't need AI still "
                       "works; try the AI parts again tomorrow.",
        "busy": "The free AI service is busy right now. Please try again in a minute.",
        "error": "The AI's reply couldn't be used this time. Please try again.",
    }

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(self.MESSAGES.get(reason, reason))
        self.reason = reason
        self.detail = detail


def ui_status(reason: str) -> str:
    """The short status the website uses for an AIUnavailable reason."""
    return {"daily_limit": "resting"}.get(reason, reason)


def message_for(status: str) -> str:
    return AIUnavailable.MESSAGES.get({"resting": "daily_limit"}.get(status, status), "")


class RateLimited(Exception):
    def __init__(self, retry_after: float | None = None, daily: bool = False, detail: str = "") -> None:
        super().__init__(detail or "rate limited")
        self.retry_after = retry_after
        self.daily = daily
        self.detail = detail


class ProviderError(Exception):
    """A request that failed for a reason waiting won't fix.
    kind: auth | model | region | bad_request | network | bad_output"""

    def __init__(self, kind: str, detail: str = "", status: int = 0) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.status = kind, detail, status


SETUP_KINDS = {"auth", "model", "region", "bad_request"}       # the site owner must fix these


def classify(status: int, body: str, retry_after: float | None = None) -> Exception:
    """Turn an HTTP error from any provider into RateLimited or ProviderError."""
    try:
        err = json.loads(body).get("error", {})
        if isinstance(err, str):
            err = {"message": err}
    except (ValueError, AttributeError):
        err = {}
    message = str(err.get("message") or body or "")[:400]
    code = str(err.get("status") or err.get("type") or err.get("code") or "")
    reasons = {str(d.get("reason", "")) for d in err.get("details", []) if isinstance(d, dict)}
    detail = f"HTTP {status} {code} {sorted(r for r in reasons if r)} {message}".strip()
    low = message.lower()
    if status == 429 or code == "RESOURCE_EXHAUSTED" or "rate_limit" in code.lower():
        return RateLimited(retry_after, daily=_looks_daily(body), detail=detail)
    if status in (500, 502, 503, 504, 529) or code in ("UNAVAILABLE", "INTERNAL", "overloaded_error"):
        return RateLimited(retry_after, detail="overloaded: " + detail)
    if "location is not supported" in low or "region" in low and "not supported" in low:
        return ProviderError("region", detail, status)
    if status in (401, 403) or reasons & {"API_KEY_INVALID", "ACCESS_TOKEN_TYPE_UNSUPPORTED", "CREDENTIALS_MISSING",
                                          "SERVICE_DISABLED", "API_KEY_SERVICE_BLOCKED", "PERMISSION_DENIED"} \
            or "api key" in low or "authentication" in low:
        return ProviderError("auth", detail, status)
    if status == 404 or "not found" in low or "not supported for generatecontent" in low \
            or "model_not_found" in low or "does not exist" in low:
        return ProviderError("model", detail, status)
    return ProviderError("bad_request", detail, status)


# ---------------------------------------------------------------------------
# Providers: each turns (system, prompt) into text over plain HTTPS.
# ---------------------------------------------------------------------------

class Provider:
    name = ""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=config.AI_TIMEOUT)
        self.model_override: dict[str, str] = {}

    @property
    def key(self) -> str:
        return config.api_key(self.name)

    def available(self) -> bool:
        return bool(self.key)

    def model(self, tier: str) -> str:
        return self.model_override.get(tier) or config.MODELS[self.name][tier]

    def endpoint(self) -> str:
        return self.name

    def describe(self) -> str:
        """For the startup log: never the key itself."""
        return f"{self.name}: models fast={self.model('fast')} smart={self.model('smart')}, key {key_hint(self.key)}"

    def send(self, system: str, prompt: str, model: str, max_tokens: int, json_mode: bool) -> str:
        raise NotImplementedError

    def _post(self, url: str, headers: dict, body: dict, params: Optional[dict] = None) -> dict:
        try:
            resp = self.client.post(url, headers=headers, json=body, params=params)
        except httpx.HTTPError as e:
            raise ProviderError("network", redact(f"{type(e).__name__}: {e}")[:300]) from None
        if resp.status_code >= 400:
            raise classify(resp.status_code, resp.text, _retry_after(resp))
        try:
            return resp.json()
        except ValueError:
            raise ProviderError("bad_output", f"not JSON: {resp.text[:200]}") from None


def redact(text: str) -> str:
    """Remove every configured API key from a text before it is logged or shown."""
    for name in ("gemini", "groq", "anthropic"):
        k = config.api_key(name)
        if k and len(k) > 6:
            text = text.replace(k, "***")
    return text


def key_hint(key: str) -> str:
    """Describe a key without revealing it: its type and length only."""
    if not key:
        return "missing"
    if key.startswith("AIza"):
        kind = "AI Studio style (AIza...)"
    elif key.startswith("AQ."):
        kind = "Vertex AI express style (AQ....)"
    elif key.startswith("gsk_"):
        kind = "Groq style (gsk_...)"
    elif key.startswith("sk-ant-"):
        kind = "Anthropic style (sk-ant-...)"
    else:
        kind = "unknown format"
    note = " - has spaces or quotes around it!" if key != key.strip().strip('"\'') else ""
    return f"set, {kind}, {len(key)} characters{note}"


class GeminiProvider(Provider):
    """Google Gemini. Two kinds of keys exist:
      AIza...  Google AI Studio keys -> generativelanguage.googleapis.com (key in a header)
      AQ....   Vertex AI express keys -> aiplatform.googleapis.com (key as ?key=)
    Each endpoint rejects the other kind of key, so we try the one that fits the
    key first, fall back to the other, and remember which one worked.
    GEMINI_ENDPOINT=aistudio|vertex forces one."""
    name = "gemini"
    ENDPOINTS = {
        "aistudio": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "vertex": "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent",
    }
    LIST_MODELS = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, client: httpx.Client | None = None) -> None:
        super().__init__(client)
        self.working: Optional[str] = None

    def endpoints(self) -> list[str]:
        forced = os.environ.get("GEMINI_ENDPOINT", "auto").strip().lower()
        if forced in self.ENDPOINTS:
            return [forced]
        order = ["vertex", "aistudio"] if self.key.startswith("AQ.") else ["aistudio", "vertex"]
        if self.working in order:
            order.remove(self.working)
            order.insert(0, self.working)
        return order

    def endpoint(self) -> str:
        return f"gemini/{self.endpoints()[0]}"

    def describe(self) -> str:
        return super().describe() + f", endpoint order {self.endpoints()}"

    def send(self, system, prompt, model, max_tokens, json_mode):
        tried = []
        for i, ep in enumerate(self.endpoints()):
            try:
                text = self._send_to(ep, system, prompt, model, max_tokens, json_mode)
            except ProviderError as e:
                tried.append(f"{ep}: {e.detail[:160]}")
                if e.kind == "auth" and i + 1 < len(self.endpoints()):
                    log.info("Gemini key not accepted by the %s endpoint; trying the other one", ep)
                    continue
                if e.kind == "auth":
                    raise ProviderError("auth", " | ".join(tried), e.status) from None
                raise
            if self.working != ep:
                self.working = ep
                log.info("Gemini works with the %s endpoint", ep)
            return text
        raise ProviderError("auth", " | ".join(tried))

    def _send_to(self, ep, system, prompt, model, max_tokens, json_mode, thinking=True, retry_model=True) -> str:
        gen: dict = {"maxOutputTokens": max_tokens, "temperature": 0.2}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        if thinking and "2.5-flash" in model:
            gen["thinkingConfig"] = {"thinkingBudget": 0}   # thinking would eat the free quota
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": gen}
        url = self.ENDPOINTS[ep].format(model=model)
        try:
            if ep == "vertex":
                data = self._post(url, {"content-type": "application/json"}, body, params={"key": self.key})
            else:
                data = self._post(url, {"x-goog-api-key": self.key, "content-type": "application/json"}, body)
        except ProviderError as e:
            if e.kind == "bad_request" and thinking and "thinking" in e.detail.lower():
                return self._send_to(ep, system, prompt, model, max_tokens, json_mode, thinking=False)
            if e.kind == "model" and retry_model and ep == "aistudio":
                tier = "fast" if model == self.model("fast") else "smart"
                better = self.discover_model(tier, model)
                if better:
                    return self._send_to(ep, system, prompt, better, max_tokens, json_mode, retry_model=False)
            raise
        candidates = data.get("candidates") or []
        if not candidates:
            block = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise ProviderError("bad_output", f"empty answer ({block})")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise ProviderError("bad_output", f"empty text, finishReason={candidates[0].get('finishReason')}")
        return text

    def discover_model(self, tier: str, missing: str) -> Optional[str]:
        """The configured model doesn't exist (models are retired over time):
        ask Google which models this key can use and pick the closest one."""
        try:
            resp = self.client.get(self.LIST_MODELS, headers={"x-goog-api-key": self.key}, params={"pageSize": 200})
            models = resp.json().get("models", []) if resp.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            models = []
        names = [m["name"].split("/", 1)[-1] for m in models
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        choice = pick_model(names, tier)
        if choice and choice != missing:
            self.model_override[tier] = choice
            log.warning("Gemini model %r is not available for this key; using %r instead "
                        "(set GEMINI_MODEL_%s to choose another)", missing, choice, tier.upper())
            return choice
        log.error("Gemini model %r is not available and no replacement was found (models seen: %s)",
                  missing, ", ".join(names[:15]) or "none")
        return None


def pick_model(names: list[str], tier: str) -> Optional[str]:
    """Best 'flash' model: newest stable version; 'lite' for the fast tier."""
    skip = ("tts", "image", "audio", "live", "embedding", "exp", "thinking", "learnlm", "gemma", "computer")
    flash = [n for n in names if "flash" in n and not any(s in n for s in skip)]
    if not flash:
        return None

    def score(n: str):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        version = float(m.group(1)) if m else 0.0
        lite = "lite" in n
        stable = "preview" not in n and not re.search(r"-\d{2,}$", n)
        return (stable, lite == (tier == "fast"), version, -len(n))
    return sorted(flash, key=score, reverse=True)[0]


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
            raise ProviderError("bad_output", f"unexpected answer: {str(data)[:300]}") from None


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
        self._broken: dict[str, tuple[float, str]] = {}   # provider -> (until, why): setup problems
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
        failures: list[tuple[str, str]] = []          # (student reason, technical detail)
        for provider in chain:
            if self._is_out(provider.name):
                failures.append(("daily_limit", f"{provider.name}: daily limit reached earlier today"))
                continue
            broken = self._broken.get(provider.name)
            if broken and broken[0] > time.monotonic():
                failures.append(("setup", f"{provider.name}: {broken[1]}"))
                continue
            model = provider.model(tier)
            started = time.monotonic()
            try:
                text = self._send_politely(provider, system, prompt, tier, max_tokens, json)
            except RateLimited as e:
                if e.daily:
                    self._out_today[provider.name] = _today()
                why = "daily_limit" if e.daily else "busy"
                self._log_failure(kind, provider, model, "daily_limit" if e.daily else
                                  ("overloaded" if e.detail.startswith("overloaded") else "rate_limit"), e.detail)
                failures.append((why, e.detail))
                continue
            except ProviderError as e:
                self._log_failure(kind, provider, provider.model(tier), e.kind, e.detail)
                if e.kind in SETUP_KINDS:
                    # don't hammer a provider that can't work until someone fixes it
                    self._broken[provider.name] = (time.monotonic() + 600, f"{e.kind}: {e.detail[:200]}")
                    failures.append(("setup", e.detail))
                else:
                    failures.append(("busy" if e.kind == "network" else "error", e.detail))
                continue
            if json:
                try:
                    text = extract_json_text(text)
                except ValueError:
                    self._log_failure(kind, provider, model, "bad_output", f"not JSON: {text[:160]!r}")
                    failures.append(("error", "reply was not JSON"))
                    continue         # never cache it
            self._broken.pop(provider.name, None)
            log.info("AI ok: %s provider=%s model=%s endpoint=%s %.1fs", kind, provider.name,
                     provider.model(tier), provider.endpoint(), time.monotonic() - started)
            db.cache_put(key, project_id, kind, provider.name, provider.model(tier), text)
            return text
        # what to tell the student: the most hopeful reason first
        reasons = [r for r, _ in failures]
        for reason in ("busy", "daily_limit", "error", "setup"):
            if reason in reasons:
                raise AIUnavailable(reason, "; ".join(d for _, d in failures)[:500])
        raise AIUnavailable("busy")

    def _log_failure(self, kind: str, p: "Provider", model: str, why: str, detail: str) -> None:
        level = logging.ERROR if why in SETUP_KINDS else logging.WARNING
        log.log(level, "AI request failed: %s provider=%s model=%s endpoint=%s reason=%s | %s",
                kind, p.name, model, p.endpoint(), why, redact(detail)[:400])

    def check(self) -> dict:
        """One tiny request to the main provider, bypassing the cache, to see if
        the AI works (for the site owner; never shows the key)."""
        chain = self.chain()
        if not chain:
            return {"ok": False, "reason": "off", "detail": "No AI provider has a key configured."}
        p = chain[0]
        self._broken.pop(p.name, None)
        try:
            text = self._send_politely(p, "Reply with one word.", "Say OK.", "fast", 20, False)
            return {"ok": True, "provider": p.name, "model": p.model("fast"), "endpoint": p.endpoint(),
                    "reply": text.strip()[:40]}
        except RateLimited as e:
            return {"ok": False, "provider": p.name, "model": p.model("fast"), "endpoint": p.endpoint(),
                    "reason": "daily_limit" if e.daily else "rate_limit", "detail": redact(e.detail)[:400]}
        except ProviderError as e:
            self._log_failure("check", p, p.model("fast"), e.kind, e.detail)
            return {"ok": False, "provider": p.name, "model": p.model("fast"), "endpoint": p.endpoint(),
                    "reason": e.kind, "detail": redact(e.detail)[:400]}

    def describe(self) -> list[str]:
        return [p.describe() for p in self.chain()] or ["AI is off: no provider has a key"]

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
