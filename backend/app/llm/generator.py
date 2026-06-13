"""
Email generator — thin LangChain wrapper around the configured LLM.
Provider is resolved once on first use, then cached.
"""

import logging
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.language_models import BaseChatModel

from app.llm.factory import detect_provider, create_llm
from app.llm.prompts import TEMPLATES, get_designation_key

log = logging.getLogger(__name__)


class EmailGenerator:
    """
    Generates cold emails and follow-ups using any LangChain-compatible LLM.
    LLM is lazily initialised on first call.
    """

    def __init__(self):
        self._llm: BaseChatModel | None = None
        self._chains: dict = {}

    async def _ensure_llm(self) -> None:
        if self._llm is None:
            provider, model = await detect_provider()
            self._llm = create_llm(provider, model)
            log.info(f"EmailGenerator using {provider}/{model}")

    def _get_chain(self, key: str):
        if key not in self._chains:
            prompt = ChatPromptTemplate.from_template(TEMPLATES[key])
            self._chains[key] = prompt | self._llm | StrOutputParser()
        return self._chains[key]

    async def generate(
        self,
        *,
        name: str,
        designation: str,
        company: str,
        resume: str,
        company_context: str = "",
        source: str = "",
    ) -> str:
        await self._ensure_llm()
        key = get_designation_key(designation)
        chain = self._get_chain(key)

        if company_context.strip():
            ctx_block = (
                "\nWhat we actually know about this recipient/company (this is REAL — "
                "build the email around THIS, and do not invent any other facts):\n"
                f"{company_context.strip()[:1500]}\n\n"
            )
        else:
            ctx_block = (
                "\n(No verified context about this company. Do NOT invent product names, "
                "metrics, or facts about them. Anchor the email on the candidate's own "
                "background and a plausible, honest reason for reaching out.)\n\n"
            )

        return await chain.ainvoke({
            "name":          name or "there",
            "designation":   designation,
            "company":       company,
            "resume":        resume[:3000],
            "context_block": ctx_block,
            "source_hint":   _source_hint(source),
        })

    async def generate_followup(
        self,
        *,
        name: str,
        company: str,
        original_email: str,
    ) -> str:
        await self._ensure_llm()
        chain = self._get_chain("followup")
        return await chain.ainvoke({
            "name":           name,
            "company":        company,
            "original_email": original_email[:600],
        })

    @property
    def ready(self) -> bool:
        return self._llm is not None


def _source_hint(source: str) -> str:
    """
    Turn the provenance of a contact into an honest framing for how/why we're
    reaching out — so the opening varies by recipient instead of being generic.
    """
    s = (source or "").lower()
    if s.startswith("github"):
        return ("You found this person through their open-source work on GitHub. "
                "It's natural to mention you came across their code/repo. They are "
                "a hands-on engineer — talk shop, be concrete and technical.")
    if "hackernews" in s or s == "hn":
        return ("You found this person through their 'Who is Hiring' post on Hacker News. "
                "Reference what they said they're looking for and map yourself to it directly.")
    if "wellfound" in s:
        return ("You found this person through a Wellfound (AngelList) job listing — "
                "an early-stage startup context. Be scrappy and direct.")
    if "hunter" in s:
        return ("Reach out professionally; you don't have a specific shared touchpoint, "
                "so let the company context and your fit carry the email.")
    return ("Keep the framing honest — don't claim a connection or touchpoint you "
            "don't actually have.")


# Singleton — shared across all requests
generator = EmailGenerator()
