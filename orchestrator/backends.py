"""LLM backends: the Anthropic API and Ollama's OpenAI-compatible endpoint, behind one interface.

A backend does two things:
- `conversation(system, tools)` opens a multi-call tool-use conversation (one simulated week).
- `complete(system, user, max_tokens, schema)` makes a single call (referee, memory summary).

Tool definitions are written once in Anthropic's shape ({name, description, input_schema})
and converted for the OpenAI-compatible side.
"""

import json
import os
import time
from dataclasses import dataclass

import anthropic
import httpx2 as httpx

# Opus 5.5 defaults to "medium"; set it explicitly so runs don't change if the default does.
EFFORT = "medium"
# Models that accept server-side refusal fallbacks ("default" routes by refusal category).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Dollars per million tokens: (input, output, cache read, 5-minute cache write). Input/output
# and the Opus/Sonnet cache-read prices are the published API rates; the rest assume the usual
# 0.1x input for cache reads and 1.25x input for 5-minute cache writes (all our cache_control
# is the default 5-minute TTL). Unverified for Haiku 5.5: reconcile a run's logged cost_usd
# against the Console usage page before trusting it.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
}


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    def as_dict(self) -> dict:
        return {"input": self.input, "output": self.output, "cache_read": self.cache_read,
                "cache_write": self.cache_write, "total": self.total}


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    error: str | None = None  # set when the model's arguments weren't valid JSON


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: str
    latency_s: float
    raw_usage: dict | None = None    # the API's usage object, verbatim, for reconciling costs
    served_model: str | None = None  # can differ from the requested model when a fallback served it


def make_backend(spec: str):
    """`ollama:<model>` → Ollama; `anthropic:<model>` or `claude-*` → Anthropic."""
    if spec.startswith("ollama:"):
        return OllamaBackend(spec.removeprefix("ollama:"))
    if spec.startswith("anthropic:"):
        return AnthropicBackend(spec.removeprefix("anthropic:"))
    if spec.startswith("claude-"):
        return AnthropicBackend(spec)
    raise ValueError(f"unknown model {spec!r}: use ollama:<model>, anthropic:<model>, or claude-*")


# --- Anthropic -------------------------------------------------------------------------------

class AnthropicBackend:
    name = "anthropic"
    default_max_tokens = 16000  # thinking is always on for current models and counts as output

    def __init__(self, model: str):
        if model not in PRICES:
            raise ValueError(f"no price for {model!r}; add it to backends.PRICES so runs can report cost")
        self.model = model
        self.client = anthropic.Anthropic()

    def cost(self, reply: Reply) -> float:
        """From the API's usage fields: input_tokens excludes cached tokens, which are
        reported separately as cache_read_input_tokens and cache_creation_input_tokens."""
        model = reply.served_model if reply.served_model in PRICES else self.model
        i, o, cr, cw = PRICES[model]
        u = reply.usage
        return (u.input * i + u.output * o + u.cache_read * cr + u.cache_write * cw) / 1e6

    def _create(self, **kw):
        kw.setdefault("output_config", {})["effort"] = EFFORT
        if self.model in FALLBACK_MODELS:
            return self.client.beta.messages.create(
                model=self.model, betas=[FALLBACK_BETA], fallbacks="default", **kw)
        return self.client.messages.create(model=self.model, **kw)

    @staticmethod
    def _reply(resp, t0: float) -> Reply:
        u = resp.usage
        usage = Usage(u.input_tokens, u.output_tokens,
                      u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, b.input) for b in resp.content if b.type == "tool_use"]
        return Reply(text, calls, usage, resp.stop_reason, time.monotonic() - t0,
                     raw_usage=u.model_dump(mode="json", exclude_none=True), served_model=resp.model)

    def conversation(self, system: str, tools: list[dict]):
        return AnthropicConversation(self, system, tools)

    deterministic = False  # current Claude models reject temperature/top_p/top_k with a 400

    def complete(self, system: str, user: str, max_tokens: int, schema: dict | None = None,
                 temperature: float | None = None, seed: int | None = None) -> Reply:
        # temperature and seed are accepted for interface parity and ignored (see `deterministic`).
        kw = {"output_config": {"format": {"type": "json_schema", "schema": schema}}} if schema else {}
        t0 = time.monotonic()
        resp = self._create(system=system, max_tokens=max_tokens,
                            messages=[{"role": "user", "content": user}], **kw)
        return self._reply(resp, t0)


class AnthropicConversation:
    def __init__(self, backend: AnthropicBackend, system: str, tools: list[dict]):
        self.backend = backend
        # The system prompt and tools are identical every week, so they cache across weeks.
        self.system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self.tools = tools
        self.messages: list[dict] = []

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, str, bool]]):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id, "content": out, "is_error": is_error}
            for call, out, is_error in results]})

    def size_chars(self) -> int:
        return len(self.system[0]["text"]) + len(json.dumps(self.tools)) + len(str(self.messages))

    def send(self, max_tokens: int) -> Reply:
        t0 = time.monotonic()
        resp = self.backend._create(system=self.system, tools=self.tools, messages=self.messages,
                                    max_tokens=max_tokens, cache_control={"type": "ephemeral"})
        # Append the content blocks unchanged: thinking blocks must be passed back as returned.
        self.messages.append({"role": "assistant", "content": resp.content})
        return self.backend._reply(resp, t0)


# --- Ollama (OpenAI-compatible) --------------------------------------------------------------

class OllamaBackend:
    name = "ollama"
    default_max_tokens = 2048

    def __init__(self, model: str):
        self.model = model
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        self.url = base + "/chat/completions"
        self.http = httpx.Client(timeout=600)

    def _post(self, body: dict) -> dict:
        # reasoning_effort "none" turns off qwen3-style thinking: ~20x faster, and v0 doesn't use it.
        body = {"model": self.model, "reasoning_effort": "none", **body}
        for attempt in range(3):
            try:
                r = self.http.post(self.url, json=body)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    @staticmethod
    def _reply(data: dict, t0: float) -> Reply:
        choice = data["choices"][0]
        msg = choice["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc["function"]
            try:
                args, err = json.loads(fn.get("arguments") or "{}"), None
            except json.JSONDecodeError as e:
                args, err = {}, f"arguments were not valid JSON: {e}"
            calls.append(ToolCall(tc["id"], fn["name"], args if isinstance(args, dict) else {}, err))
        u = data.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        usage = Usage(u.get("prompt_tokens", 0) - cached, u.get("completion_tokens", 0), cached, 0)
        return Reply(msg.get("content") or "", calls, usage, choice.get("finish_reason") or "",
                     time.monotonic() - t0)

    def cost(self, reply: Reply) -> float:
        return 0.0  # local

    def conversation(self, system: str, tools: list[dict]):
        return OllamaConversation(self, system, tools)

    deterministic = True  # with temperature 0 and a fixed seed

    def complete(self, system: str, user: str, max_tokens: int, schema: dict | None = None,
                 temperature: float | None = None, seed: int | None = None) -> Reply:
        body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if seed is not None:
            body["seed"] = seed
        if schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "output", "schema": schema}}
        t0 = time.monotonic()
        return self._reply(self._post(body), t0)


class OllamaConversation:
    def __init__(self, backend: OllamaBackend, system: str, tools: list[dict]):
        self.backend = backend
        self.tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools]
        self.messages: list[dict] = [{"role": "system", "content": system}]

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, str, bool]]):
        for call, out, is_error in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id,
                                  "content": ("ERROR: " + out) if is_error else out})

    def size_chars(self) -> int:
        return len(json.dumps(self.messages)) + len(json.dumps(self.tools))

    def send(self, max_tokens: int) -> Reply:
        t0 = time.monotonic()
        data = self.backend._post({"messages": self.messages, "tools": self.tools,
                                   "max_tokens": max_tokens})
        msg = data["choices"][0]["message"]
        turn = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            turn["tool_calls"] = msg["tool_calls"]
        self.messages.append(turn)
        return self.backend._reply(data, t0)
