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
import json as _json
import logging
from dataclasses import dataclass, field
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

    def send_tools(self, system: str, messages: list[dict], tools: list[dict], model: str,
                   max_tokens: int, force: Optional[str] = None) -> "ToolReply":
        """One turn of a function-calling conversation (see ToolReply)."""
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


# ---------------------------------------------------------------------------
# Function calling: provider-neutral tools and messages
#   tool     {"name", "description", "parameters": JSON schema}
#   messages {"role": "user", "text"}
#            {"role": "assistant", "text", "calls": [{"id", "name", "args"}], "raw": {...}}
#            {"role": "tool", "results": [{"id", "name", "content"}]}
# "raw" keeps a provider's own reply (Gemini thought signatures must be sent back).
# ---------------------------------------------------------------------------

@dataclass
class ToolReply:
    text: str = ""
    calls: list[dict] = field(default_factory=list)      # [{"id", "name", "args"}]
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"text": self.text, "calls": self.calls, "raw": self.raw}

    @classmethod
    def from_dict(cls, d: dict) -> "ToolReply":
        return cls(d.get("text", ""), d.get("calls", []), d.get("raw", {}))

    def as_message(self) -> dict:
        return {"role": "assistant", "text": self.text, "calls": self.calls, "raw": self.raw}


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

    # -- the two kinds of request --
    def send(self, system, prompt, model, max_tokens, json_mode):
        def body(m: str, thinking: bool) -> dict:
            gen = self._gen(m, max_tokens, thinking)
            if json_mode:
                gen["responseMimeType"] = "application/json"
            return {"systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
        data = self._call(model, body)
        text, _calls, _parts = self._read(data)
        if not text.strip():
            raise ProviderError("bad_output", f"empty text, finishReason={self._finish(data)}")
        return text

    def send_tools(self, system, messages, tools, model, max_tokens, force=None) -> ToolReply:
        contents = []
        for m in messages:
            if m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["text"]}]})
            elif m["role"] == "assistant":
                parts = (m.get("raw") or {}).get("gemini_parts")
                if not parts:
                    parts = ([{"text": m["text"]}] if m.get("text") else []) + \
                            [{"functionCall": {"name": c["name"], "args": c["args"]}} for c in m.get("calls", [])]
                contents.append({"role": "model", "parts": parts})
            elif m["role"] == "tool":
                contents.append({"role": "user", "parts": [
                    {"functionResponse": {"name": r["name"], "response": {"content": r["content"]}}}
                    for r in m["results"]]})
        decls = [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in tools]
        mode = {"mode": "ANY", "allowedFunctionNames": [force]} if force else {"mode": "AUTO"}

        def body(m: str, thinking: bool) -> dict:
            return {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                    "tools": [{"functionDeclarations": decls}], "toolConfig": {"functionCallingConfig": mode},
                    "generationConfig": self._gen(m, max_tokens, thinking)}
        data = self._call(model, body)
        text, calls, parts = self._read(data)
        if not text.strip() and not calls:
            raise ProviderError("bad_output", f"no text and no tool call, finishReason={self._finish(data)}")
        return ToolReply(text, calls, {"gemini_parts": parts})

    # -- shared plumbing --
    @staticmethod
    def _gen(model: str, max_tokens: int, thinking: bool) -> dict:
        gen: dict = {"maxOutputTokens": max_tokens, "temperature": 0.2}
        if thinking and "2.5-flash" in model:
            gen["thinkingConfig"] = {"thinkingBudget": 0}   # thinking would eat the free quota
        return gen

    def _call(self, model: str, body: Callable[[str, bool], dict]) -> dict:
        """Try the endpoints (auth errors move on to the next one), replace a retired
        model once, and drop thinkingConfig if a model refuses it."""
        tried = []
        endpoints = self.endpoints()
        for i, ep in enumerate(endpoints):
            try:
                data = self._post_to(ep, model, body)
            except ProviderError as e:
                tried.append(f"{ep}: {e.detail[:200]}")
                if e.kind == "auth" and i + 1 < len(endpoints):
                    log.info("Gemini key not accepted by the %s endpoint; trying the other one", ep)
                    continue
                if e.kind == "auth":
                    raise ProviderError("auth", " | ".join(tried), e.status) from None
                raise
            if self.working != ep:
                self.working = ep
                log.info("Gemini works with the %s endpoint", ep)
            return data
        raise ProviderError("auth", " | ".join(tried))

    def _post_to(self, ep: str, model: str, body, thinking: bool = True, retry_model: bool = True) -> dict:
        url = self.ENDPOINTS[ep].format(model=model)
        try:
            if ep == "vertex":
                return self._post(url, {"content-type": "application/json"}, body(model, thinking),
                                  params={"key": self.key})
            return self._post(url, {"x-goog-api-key": self.key, "content-type": "application/json"},
                              body(model, thinking))
        except ProviderError as e:
            if e.kind == "bad_request" and thinking and "thinking" in e.detail.lower():
                return self._post_to(ep, model, body, thinking=False, retry_model=retry_model)
            if e.kind == "model" and retry_model and ep == "aistudio":
                tier = "fast" if model == self.model("fast") else "smart"
                better = self.discover_model(tier, model)
                if better:
                    return self._post_to(ep, better, body, thinking=thinking, retry_model=False)
            raise

    @staticmethod
    def _read(data: dict) -> tuple[str, list[dict], list]:
        candidates = data.get("candidates") or []
        if not candidates:
            block = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise ProviderError("bad_output", f"empty answer ({block})")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        calls = [{"id": p["functionCall"].get("id") or f"call_{i}", "name": p["functionCall"].get("name", ""),
                  "args": p["functionCall"].get("args") or {}}
                 for i, p in enumerate(parts) if "functionCall" in p]
        return text, calls, parts

    @staticmethod
    def _finish(data: dict) -> str:
        return str(((data.get("candidates") or [{}])[0]).get("finishReason"))

    def list_models(self) -> list[str]:
        try:
            resp = self.client.get(self.LIST_MODELS, headers={"x-goog-api-key": self.key}, params={"pageSize": 200})
            models = resp.json().get("models", []) if resp.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            models = []
        return [m["name"].split("/", 1)[-1] for m in models
                if "generateContent" in m.get("supportedGenerationMethods", [])]

    def discover_model(self, tier: str, missing: str) -> Optional[str]:
        """The configured model doesn't exist (models are retired over time):
        ask Google which models this key can use and pick the closest one."""
        names = self.list_models()
        choice = pick_model(names, tier)
        if choice and choice != missing:
            self.model_override[tier] = choice
            log.warning("Gemini model %r is not available for this key; using %r instead "
                        "(set GEMINI_MODEL_%s to choose another)", missing, choice, tier.upper())
            return choice
        log.error("Gemini model %r is not available and no replacement was found (models seen: %s)",
                  missing, ", ".join(names[:15]) or "none")
        return None

    def diagnose_attempt(self, ep: str, model: str, body: dict) -> dict:
        """One raw request for the diagnostics page: never raises, never shows the key."""
        url = self.ENDPOINTS[ep].format(model=model)
        started = time.monotonic()
        try:
            if ep == "vertex":
                resp = self.client.post(url, json=body, params={"key": self.key})
            else:
                resp = self.client.post(url, json=body, headers={"x-goog-api-key": self.key})
        except httpx.HTTPError as e:
            return {"endpoint": ep, "url": url, "model": model, "status": "network error",
                    "ok": False, "message": redact(f"{type(e).__name__}: {e}")[:300],
                    "ms": int((time.monotonic() - started) * 1000)}
        out = {"endpoint": ep, "url": url, "model": model, "status": resp.status_code,
               "ms": int((time.monotonic() - started) * 1000), "ok": resp.status_code == 200}
        if resp.status_code == 200:
            try:
                text, calls, _ = self._read(resp.json())
                out["reply"] = ((text.strip()[:80] or "") + (f" [tool call: {calls[0]['name']}({calls[0]['args']})]"
                                                             if calls else "")).strip()
            except (ProviderError, ValueError) as e:
                out["ok"], out["message"] = False, redact(str(e))[:300]
        else:
            err = classify(resp.status_code, resp.text)
            out["reason"] = getattr(err, "kind", "daily_limit" if getattr(err, "daily", False) else "rate_limit")
            out["message"] = redact(getattr(err, "detail", resp.text))[:500]
        return out


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

    def send_tools(self, system, messages, tools, model, max_tokens, force=None) -> "ToolReply":
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                msgs.append({"role": "assistant", "content": m.get("text") or None,
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                            for c in m.get("calls", [])] or None})
            elif m["role"] == "tool":
                msgs += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"]]
        body: dict = {"model": model, "max_tokens": max_tokens, "temperature": 0.2, "messages": msgs,
                      "tools": [{"type": "function", "function": t} for t in tools],
                      "tool_choice": {"type": "function", "function": {"name": force}} if force else "auto"}
        data = self._post(self.URL, {"authorization": f"Bearer {self.key}"}, body)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError):
            raise ProviderError("bad_output", f"unexpected answer: {str(data)[:300]}") from None
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append({"id": c.get("id", ""), "name": c["function"]["name"], "args": args})
        return ToolReply(msg.get("content") or "", calls)


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

    def send_tools(self, system, messages, tools, model, max_tokens, force=None) -> "ToolReply":
        msgs: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                blocks = ([{"type": "text", "text": m["text"]}] if m.get("text") else []) + \
                         [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                          for c in m.get("calls", [])]
                msgs.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                msgs.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]} for r in m["results"]]})
        # Forcing a tool is not supported by every Claude model, so `force` only
        # narrows the tools offered (the agent offers just final_answer then).
        data = self._post(self.URL, {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                          {"model": model, "max_tokens": max_tokens, "system": system, "messages": msgs,
                           "tools": [{"name": t["name"], "description": t["description"],
                                      "input_schema": t["parameters"]} for t in tools]})
        content = data.get("content", [])
        return ToolReply("".join(b.get("text", "") for b in content if b.get("type") == "text"),
                         [{"id": b["id"], "name": b["name"], "args": b.get("input") or {}}
                          for b in content if b.get("type") == "tool_use"])


class FakeProvider(Provider):
    """Answers instantly from handlers registered per request kind.
    Tests can also make it pretend to be rate limited."""
    name = "fake"
    handlers: dict[str, Callable[[str, str], str]] = {}
    fail_next: list[Exception] = []          # exceptions to raise, in order (tests)
    calls: list[str] = []                    # kinds asked, in order (tests)

    def available(self) -> bool:
        return True

    tool_handlers: dict[str, Callable[[str, list, list], dict]] = {}

    def send(self, system, prompt, model, max_tokens, json_mode):
        if self.fail_next:
            raise self.fail_next.pop(0)
        kind = re.search(r"\[kind:([\w-]+)\]", system)
        kind = kind.group(1) if kind else ""
        self.calls.append(kind)
        handler = self.handlers.get(kind)
        return handler(system, prompt) if handler else "This is a fake AI answer."

    def send_tools(self, system, messages, tools, model, max_tokens, force=None) -> "ToolReply":
        if self.fail_next:
            raise self.fail_next.pop(0)
        kind = re.search(r"\[kind:([\w-]+)\]", system)
        kind = kind.group(1) if kind else ""
        self.calls.append(kind)
        handler = self.tool_handlers.get(kind)
        out = handler(system, messages, tools) if handler else {"text": "This is a fake AI answer."}
        if force and not any(c["name"] == force for c in out.get("calls", [])):
            out = {"text": "", "calls": [{"name": force, "args": {"answer": out.get("text") or "(fake)"}}]}
        return ToolReply(out.get("text", ""), [{"id": c.get("id", f"call_{i}"), "name": c["name"], "args": c.get("args", {})}
                                                for i, c in enumerate(out.get("calls", []))])


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

    # -- the public calls --
    def ask(self, kind: str, system: str, prompt: str, *, tier: str = "fast",
            json: bool = False, max_tokens: int = 1024,
            project_id: Optional[str] = None) -> str:
        """Plain text (or a JSON text) answer."""
        system = f"{system}\n[kind:{kind}]"
        key = hashlib.sha256("\x1f".join([kind, tier, str(json), system, prompt]).encode()).hexdigest()

        def call(p: "Provider", model: str) -> str:
            text = p.send(system, prompt, model, max_tokens, json)
            if json:
                try:
                    text = extract_json_text(text)
                except ValueError:
                    raise ProviderError("bad_output", f"not JSON: {text[:160]!r}") from None
            return text
        return self._run(kind, tier, key, project_id, call, encode=lambda t: t, decode=lambda t: t)

    def ask_tools(self, kind: str, system: str, messages: list[dict], tools: list[dict], *,
                  tier: str = "smart", force: Optional[str] = None, max_tokens: int = 1024,
                  project_id: Optional[str] = None) -> "ToolReply":
        """One turn of a function-calling conversation: the model either calls
        tools or answers in text. `force` makes it call that tool."""
        system = f"{system}\n[kind:{kind}]"
        key = hashlib.sha256(_json.dumps([kind, tier, system, messages, tools, force], sort_keys=True,
                                         default=str).encode()).hexdigest()

        def call(p: "Provider", model: str) -> "ToolReply":
            return p.send_tools(system, messages, tools, model, max_tokens, force)
        return self._run(kind, tier, key, project_id, call,
                         encode=lambda r: _json.dumps(r.to_dict()), decode=lambda t: ToolReply.from_dict(_json.loads(t)))

    def _run(self, kind, tier, key, project_id, call, encode, decode):
        """Cache, then each provider in turn: queue, retries, logging, fallback."""
        cached = db.cache_get(key)
        if cached is not None:
            return decode(cached)
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
            started = time.monotonic()
            try:
                result = self._send_politely(provider, tier, call)
            except RateLimited as e:
                if e.daily:
                    self._out_today[provider.name] = _today()
                self._log_failure(kind, provider, provider.model(tier), "daily_limit" if e.daily else
                                  ("overloaded" if e.detail.startswith("overloaded") else "rate_limit"), e.detail)
                failures.append(("daily_limit" if e.daily else "busy", e.detail))
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
            self._broken.pop(provider.name, None)
            log.info("AI ok: %s provider=%s model=%s endpoint=%s %.1fs", kind, provider.name,
                     provider.model(tier), provider.endpoint(), time.monotonic() - started)
            db.cache_put(key, project_id, kind, provider.name, provider.model(tier), encode(result))
            return result
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
            text = self._send_politely(p, "fast", lambda prov, m: prov.send("Reply with one word.", "Say OK.", m, 20, False))
            return {"ok": True, "provider": p.name, "model": p.model("fast"), "endpoint": p.endpoint(),
                    "reply": text.strip()[:40]}
        except RateLimited as e:
            return {"ok": False, "provider": p.name, "model": p.model("fast"), "endpoint": p.endpoint(),
                    "reason": "daily_limit" if e.daily else "rate_limit", "detail": redact(e.detail)[:400]}
        except ProviderError as e:
            self._log_failure("check", p, p.model("fast"), e.kind, e.detail)
            return {"ok": False, "provider": p.name, "model": p.model("fast"), "endpoint": p.endpoint(),
                    "reason": e.kind, "detail": redact(e.detail)[:400]}

    def diagnose(self) -> list[str]:
        """A plain-text report for the site owner's diagnostics page. Makes a few
        tiny real requests, bypassing the cache and the 'broken' pause. Never
        prints a key."""
        lines = [f"RepoSage AI diagnostics - {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
                 f"AI_PROVIDER={config.AI_PROVIDER}  AI_FALLBACK={config.AI_FALLBACK}  "
                 f"GEMINI_ENDPOINT={os.environ.get('GEMINI_ENDPOINT', 'auto')}  "
                 f"CHAT_MODEL_TIER={os.environ.get('CHAT_MODEL_TIER', 'smart')}", ""]
        for name in [config.AI_PROVIDER, config.AI_FALLBACK]:
            if name not in PROVIDERS or name in ("fake",):
                continue
            p = self._providers.get(name) or PROVIDERS[name]()
            lines.append(f"== {name}: key {key_hint(p.key)}")
            if not p.key:
                lines += ["   (no key set: this provider is skipped)", ""]
                continue
            lines.append(f"   models: fast={p.model('fast')}  smart={p.model('smart')}")
            today = db.usage_today(name)
            lines.append(f"   requests today (counted by RepoSage): {today} of AI_DAILY_LIMIT={config.AI_DAILY_LIMIT}")
            if isinstance(p, GeminiProvider):
                lines += self._diagnose_gemini(p)
            else:
                lines += self._diagnose_other(p)
            lines.append("")
        lines += ["Reasons: auth = key not accepted (wrong key, or wrong kind of key for the endpoint);",
                  "model = model name not available for this key; region = not offered where the server runs;",
                  "rate_limit = too many requests per minute; daily_limit = free quota for today used up;",
                  "bad_request = Google refused the request format (send this report to the developer)."]
        return lines

    def _diagnose_gemini(self, p: "GeminiProvider") -> list[str]:
        out = []
        working = None
        for tier in ("fast", "smart"):
            model = p.model(tier)
            body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word OK."}]}],
                    "generationConfig": p._gen(model, 20, True)}
            for ep in p.endpoints():
                r = p.diagnose_attempt(ep, model, body)
                out.append(_fmt_attempt(f"{tier} model", r))
                if r["ok"]:
                    working = working or (ep, model)
                    break
        if working:
            ep, model = working
            body = {"contents": [{"role": "user", "parts": [{"text": "Call the ping tool with word='hello'."}]}],
                    "tools": [{"functionDeclarations": [{"name": "ping", "description": "Test tool.",
                                                         "parameters": {"type": "object", "properties": {
                                                             "word": {"type": "string"}}, "required": ["word"]}}]}],
                    "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["ping"]}},
                    "generationConfig": p._gen(model, 60, True)}
            r = p.diagnose_attempt(ep, model, body)
            ok_fc = r["ok"] and "tool call: ping" in r.get("reply", "")
            out.append(_fmt_attempt("function calling", r) + ("" if ok_fc or not r["ok"] else "  (no tool call came back)"))
        else:
            out.append("   function calling: not tested (no working endpoint/model above)")
        if "aistudio" in p.endpoints():
            names = p.list_models()
            flash = [n for n in names if "flash" in n]
            out.append(f"   models this key can use (AI Studio list, flash only): {', '.join(flash[:20]) or 'none / list not allowed'}")
        return out

    def _diagnose_other(self, p: "Provider") -> list[str]:
        out = []
        for tier in ("fast", "smart"):
            started = time.monotonic()
            try:
                reply = p.send("Reply with one word.", "Say OK.", p.model(tier), 20, False)
                out.append(f"   {tier} model {p.model(tier)}: OK in {int((time.monotonic() - started) * 1000)} ms, "
                           f"reply {reply.strip()[:40]!r}")
            except (RateLimited, ProviderError) as e:
                why = getattr(e, "kind", "daily_limit" if getattr(e, "daily", False) else "rate_limit")
                out.append(f"   {tier} model {p.model(tier)}: FAILED reason={why} | {redact(getattr(e, 'detail', str(e)))[:400]}")
        return out

    def describe(self) -> list[str]:
        return [p.describe() for p in self.chain()] or ["AI is off: no provider has a key"]

    def _send_politely(self, p: "Provider", tier: str, call):
        """One request at a time per provider, spaced out, with retries."""
        with self._locks[p.name]:
            for attempt in range(config.AI_MAX_RETRIES + 1):
                if db.usage_today(p.name) >= config.AI_DAILY_LIMIT:
                    raise RateLimited(daily=True, detail="RepoSage's own AI_DAILY_LIMIT reached")
                wait = self._last_sent.get(p.name, 0) + config.AI_MIN_INTERVAL - time.monotonic()
                if wait > 0 and p.name != "fake":
                    self.sleep(wait)
                self._last_sent[p.name] = time.monotonic()
                db.count_request(p.name)
                try:
                    return call(p, p.model(tier))
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


def _fmt_attempt(what: str, r: dict) -> str:
    head = f"   {what} {r['model']} via {r['endpoint']}: "
    if r["ok"]:
        return head + f"OK in {r['ms']} ms, reply {r.get('reply', '')!r}"
    return head + f"FAILED HTTP {r['status']} reason={r.get('reason', '?')} ({r['ms']} ms) | {r.get('message', '')}"


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
