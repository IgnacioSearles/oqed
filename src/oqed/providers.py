"""Model providers. Every model is reached through an OpenAI-compatible Chat
Completions endpoint; `MockProvider` replays canned answers for tests and UI
work without spending credits."""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

load_dotenv(".env")


@dataclass(frozen=True)
class ModelSpec:
    name: str  # short name used on the CLI and in events
    model: str  # provider model id
    base_url: str
    key_env: str
    reasoning_effort: bool  # whether the endpoint accepts `reasoning_effort`
    max_concurrency: int


META_URL = "https://api.meta.ai/v1"
BASETEN_URL = "https://inference.baseten.co/v1"

MODELS: dict[str, ModelSpec] = {
    s.name: s
    for s in [
        ModelSpec("spark", "muse-spark-1.3", META_URL, "MODEL_API_KEY", True, 32),
        ModelSpec("spark-contributor", "muse-spark-1.3-contributor", META_URL, "MODEL_API_KEY", True, 2),
        ModelSpec("wonder", "muse-wonder-1.0", META_URL, "MODEL_API_KEY", True, 8),
        # Baseten docs: Muse Spark Open has no structured output and rejects n > 1.
        ModelSpec(
            "spark-open", "meta-models/muse-spark-open-1026", BASETEN_URL, "BASETEN_API_KEY", False, 16
        ),
    ]
}


@dataclass
class Completion:
    text: str
    model: str
    model_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    truncated: bool = False  # hit max_tokens (reasoning can use the whole budget)


class ProviderError(RuntimeError):
    pass


class Provider:
    def __init__(self, spec: ModelSpec):
        key = os.environ.get(spec.key_env)
        if not key:
            raise ProviderError(f"{spec.key_env} is not set (see .env.example)")
        self.spec = spec
        self.model = spec.model
        # Wonder at high effort can take more than 250 s for one reply.
        self._client = AsyncOpenAI(base_url=spec.base_url, api_key=key, max_retries=0, timeout=600)
        self._sem = asyncio.Semaphore(spec.max_concurrency)

    async def complete(
        self, messages: list[dict], effort: str | None = None, max_tokens: int = 32000
    ) -> Completion:
        kwargs: dict = {"model": self.spec.model, "messages": messages, "max_tokens": max_tokens}
        if effort and self.spec.reasoning_effort:
            kwargs["reasoning_effort"] = effort
        async with self._sem:
            # model_ms covers every attempt and backoff: all of it is time spent waiting on the model.
            t0 = time.perf_counter()
            for attempt in range(6):
                try:
                    r = await self._client.chat.completions.create(**kwargs)
                except (RateLimitError, APIConnectionError) as e:
                    err: Exception = e
                except APIStatusError as e:
                    if e.status_code < 500:
                        raise ProviderError(f"{self.spec.name}: HTTP {e.status_code}: {e.message}") from e
                    err = e
                else:
                    u = r.usage
                    details = getattr(u, "prompt_tokens_details", None)
                    return Completion(
                        text=r.choices[0].message.content or "",
                        model=self.spec.model,
                        model_ms=(time.perf_counter() - t0) * 1000,
                        tokens_in=u.prompt_tokens if u else 0,
                        tokens_out=u.completion_tokens if u else 0,
                        tokens_cached=(getattr(details, "cached_tokens", 0) or 0) if details else 0,
                        truncated=r.choices[0].finish_reason == "length",
                    )
                await asyncio.sleep(min(30.0, 2**attempt) * (0.5 + random.random()))
            raise ProviderError(f"{self.spec.name}: giving up after retries: {err}")


@dataclass
class MockProvider:
    """Answers with a random pick from `answers` (canned `<asm>` replies)."""

    answers: list[str]
    model: str = "mock"
    seed: int = 0
    delay: float = 0.0
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    async def complete(
        self, messages: list[dict], effort: str | None = None, max_tokens: int = 0
    ) -> Completion:
        if self.delay:
            await asyncio.sleep(self.delay)
        return Completion(text=self._rng.choice(self.answers), model=self.model, model_ms=self.delay * 1000)


def get_provider(name: str) -> Provider:
    if name not in MODELS:
        raise ProviderError(f"unknown model {name!r}; choose from {', '.join(MODELS)}")
    return Provider(MODELS[name])
