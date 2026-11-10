"""[INDEPENDENT] JSON LLM boundary with disk caching and usage accounting."""
import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path

# Gateway/network hiccups. A request that failed this way produced nothing, so it is
# retried with backoff instead of being counted as a failed generation. Timeouts are
# not retried here: they already waited the full timeout.
TRANSIENT = {"InternalServerError", "APIConnectionError", "ServiceUnavailableError", "BadGatewayError"}


class LiteLLMClient:
    def __init__(self, model, temperature, seed, cache_dir, *, api_base=None, api_key=None, max_tokens=16000,
                 timeout=120, retries=4, backoff=30):
        self.params = dict(model=model, temperature=temperature, seed=seed, max_tokens=max_tokens)
        self.timeout = timeout  # per-request seconds; deliberately not part of the cache key
        self.retries, self.backoff = retries, backoff  # waits backoff, 2*backoff, ... (capped at 300 s)
        self.cache_dir = Path(cache_dir)
        self.api_base, self.api_key = api_base, api_key
        self.records = []
        self.usage = dict(calls=0, cache_hits=0, prompt_tokens=0, completion_tokens=0,
                          cost_usd=0.0, unknown_cost_calls=0)

    def _completion(self, litellm, request):
        for attempt in range(self.retries + 1):
            try:
                return litellm.completion(**request, api_base=self.api_base, api_key=self.api_key,
                                          timeout=self.timeout, num_retries=0)
            except Exception as exc:
                if type(exc).__name__ not in TRANSIENT or attempt == self.retries:
                    raise
                wait = min(self.backoff * 2 ** attempt, 300)
                print(f"transient {type(exc).__name__}; retry {attempt + 1}/{self.retries} in {wait}s", flush=True)
                time.sleep(wait)

    def _request(self, messages, **options):
        request = dict(self.params, messages=messages, **options)
        key = hashlib.sha256(json.dumps(dict(request, api_base=self.api_base),
                                       sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            self.usage["cache_hits"] += 1
            record = json.loads(path.read_text(encoding="utf-8"))
            self.records.append(dict(cache_key=key, cache_hit=True, usage=record["usage"], cost_usd=record["cost_usd"]))
            return record["message"] if "message" in record else {"content": record["raw_response"]}
        import litellm
        response = self._completion(litellm, request)
        self.usage["calls"] += 1
        usage = response.usage
        self.usage["prompt_tokens"] += usage.prompt_tokens or 0
        self.usage["completion_tokens"] += usage.completion_tokens or 0
        cost = getattr(response, "_hidden_params", {}).get("response_cost")
        if cost is None:
            try:
                cost = litellm.completion_cost(completion_response=response)
            except Exception:
                cost = None
        if cost is None:
            self.usage["unknown_cost_calls"] += 1
        else:
            self.usage["cost_usd"] += cost
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        message = response.choices[0].message.model_dump(exclude_none=True)
        raw = response.choices[0].message.content
        self.records.append(dict(cache_key=key, cache_hit=False, usage=usage.model_dump(), cost_usd=cost))
        # Keep billed usage even when a provider returns malformed/truncated JSON.
        path.write_text(json.dumps({"raw_response": raw, "message": message, "usage": usage.model_dump(),
                                    "cost_usd": cost}, ensure_ascii=False, indent=2), encoding="utf-8")
        return message

    def json(self, messages):
        return json.loads(self._request(messages, response_format={"type": "json_object"})["content"])

    def chat(self, messages, tools=None, seed=None):
        """[INDEPENDENT] Cache native tool-use messages, including tool calls."""
        options = dict(tools=tools, tool_choice="auto") if tools else {}
        if seed is not None:
            options["seed"] = seed
        return self._request(messages, **options)


class MockLLM:
    """[INDEPENDENT] Scripted JSON responses; never imports a provider."""
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.params = dict(model="mock", seed=0)
        self.records = []
        self.usage = dict(calls=0, cache_hits=0, prompt_tokens=0, completion_tokens=0,
                          cost_usd=0.0, unknown_cost_calls=0)

    def json(self, messages):
        self.requests.append(deepcopy(messages))
        self.usage["calls"] += 1
        return deepcopy(next(self.responses))

    def chat(self, messages, tools=None, seed=None):
        self.requests.append(deepcopy(dict(messages=messages, tools=tools)))
        self.usage["calls"] += 1
        return deepcopy(next(self.responses))
