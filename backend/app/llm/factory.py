"""
LLM Factory — provider-agnostic.

Auto-detection order (when LLM_PROVIDER=auto):
  1. Ollama  — checks localhost:11434 (free, local, private)
  2. Groq    — uses GROQ_API_KEY if set (free tier, fastest cloud)
  3. Raises  — tells user what to configure

Force a provider via .env:
  LLM_PROVIDER=ollama  LLM_MODEL=llama3.1
  LLM_PROVIDER=groq    LLM_API_KEY=gsk_xxx
  LLM_PROVIDER=openai  LLM_API_KEY=sk-xxx
"""

import httpx
import logging
from langchain_core.language_models import BaseChatModel
from app.config import settings

log = logging.getLogger(__name__)


async def detect_provider() -> tuple[str, str]:
    """
    Return (provider, model) for the best available LLM.
    Called once on startup; result cached by the generator.
    """
    if settings.llm_provider != "auto":
        model = settings.llm_model
        if not model:
            model = _default_model(settings.llm_provider)
        log.info(f"LLM forced: {settings.llm_provider}/{model}")
        return settings.llm_provider, model

    # ── Try Ollama ─────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.is_success:
                models = [m["name"] for m in resp.json().get("models", [])]
                if models:
                    # prefer llama3.x, else first available
                    preferred = next(
                        (m for m in models if "llama3" in m or "llama-3" in m),
                        models[0]
                    )
                    log.info(f"✓ Ollama detected: {preferred}")
                    return "ollama", preferred
                else:
                    log.warning("Ollama running but no models pulled. "
                                "Run: ollama pull llama3.1")
    except Exception:
        log.info("Ollama not running — trying Groq...")

    # ── Try Groq ───────────────────────────────────────────────────────────
    if settings.llm_api_key:
        log.info(f"✓ Using Groq: {settings.groq_default_model}")
        return "groq", settings.llm_model or settings.groq_default_model

    # ── No usable provider ─────────────────────────────────────────────────
    # We deliberately do NOT silently fall back to the mock here: the mock
    # returns placeholder copy that must never reach a real recipient. The mock
    # is only used when explicitly requested via LLM_PROVIDER=mock (handled by
    # the forced-provider branch above), e.g. in CI or local UI demos.
    raise RuntimeError(
        "No LLM provider available. Run Ollama (localhost:11434), set "
        "GROQ_API_KEY, or set LLM_PROVIDER explicitly (ollama|groq|openai|"
        "openrouter|anthropic). Set LLM_PROVIDER=mock only for demos/CI."
    )


def create_llm(provider: str, model: str) -> BaseChatModel:
    """Instantiate the correct LangChain chat model for a provider."""
    match provider:
        case "mock":
            return _MockLLM()

        case "ollama":
            from langchain_ollama import ChatOllama
            return ChatOllama(
                model=model,
                base_url=settings.ollama_base_url,
                temperature=settings.llm_temperature,
            )
        case "groq":
            from langchain_groq import ChatGroq
            return ChatGroq(
                model=model,
                api_key=settings.llm_api_key,
                temperature=settings.llm_temperature,
            )
        case "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model,
                api_key=settings.llm_api_key,
                temperature=settings.llm_temperature,
            )
        case "openrouter":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                base_url="https://openrouter.ai/api/v1",
                model=model,
                api_key=settings.llm_api_key,
                temperature=settings.llm_temperature,
            )
        case "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(
                model=model,
                api_key=settings.llm_api_key,
                temperature=settings.llm_temperature,
            )
        case _:
            raise ValueError(
                f"Unknown provider: {provider!r}. "
                "Choose: ollama | groq | openai | openrouter | anthropic"
            )


def _default_model(provider: str) -> str:
    defaults = {
        "mock":       "mock",
        "ollama":     settings.ollama_default_model,
        "groq":       settings.groq_default_model,
        "openai":     "gpt-4o-mini",
        "openrouter": "mistralai/mistral-7b-instruct",
        "anthropic":  "claude-3-haiku-20240307",
    }
    return defaults.get(provider, "")


# ── Mock LLM — zero config, for local demo / CI ───────────────────────────────
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

class _MockLLM(BaseChatModel):
    """Deterministic stub — returns a realistic email without any API call."""

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        # Pull company/name from the last user message
        last = messages[-1].content if messages else ""
        company = "your company"
        for line in last.splitlines():
            if "company" in line.lower() and ":" in line:
                company = line.split(":", 1)[-1].strip()
                break

        # Intentionally a placeholder, NOT a plausible email. The mock runs only
        # when no real LLM is configured; this copy is meant to be obvious so it
        # can never be mistaken for a finished draft and sent as-is.
        text = (
            f"SUBJECT: [MOCK DRAFT — configure an LLM before sending]\n\n"
            f"BODY:\n"
            f"This is a placeholder generated because no LLM provider is configured.\n\n"
            f"Set GROQ_API_KEY, run Ollama, or set LLM_PROVIDER to generate a real, "
            f"personalised email for {company}.\n\n"
            f"Do not send this message."
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])
