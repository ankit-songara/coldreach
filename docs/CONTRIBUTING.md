# Contributing to ColdReach

## Quick Start

```bash
git clone https://github.com/yourname/coldreach
cd coldreach
make install       # installs Python + Node deps + Playwright
make dev           # start backend + frontend
```

## Adding a New Email Source

The scraper system uses the Strategy pattern. Adding a new source is three steps:

**1. Create the scraper** (`backend/app/scrapers/mysource.py`):

```python
from app.scrapers.base import BaseScraper

class MySourceScraper(BaseScraper):
    name = "MySource"

    async def search(self, query: str, **_) -> list[dict]:
        # Must return list of:
        # {"name": str, "email": str, "company": str, "designation": str, "source": str}
        ...
```

**2. Register it** (`backend/app/api/hunt.py`):

```python
from app.scrapers.mysource import MySourceScraper

def _build_scrapers(hunter_key: str):
    return [
        HackerNewsScraper(),
        GitHubScraper(),
        WebScraper(),
        MySourceScraper(),  # ← add here
    ]
```

**3. Add a test** (`backend/tests/test_scrapers.py`):

```python
def test_mysource_scraper_name():
    assert MySourceScraper().name == "MySource"
```

## Adding a New LLM Provider

Edit `backend/app/llm/factory.py`:

```python
def create_llm(provider: str, model: str) -> BaseChatModel:
    match provider:
        # ... existing cases ...
        case "myprovider":
            from langchain_myprovider import ChatMyProvider
            return ChatMyProvider(model=model, api_key=settings.llm_api_key)
```

## Running Tests

```bash
make test
# or
cd backend && pytest tests/ -v
```

## Code Style

- Backend: `ruff` (configured in `pyproject.toml`)
- Frontend: ESLint + TypeScript strict mode
- Run `make lint` before submitting a PR

## PR Guidelines

- One feature / bug fix per PR
- Include a test for new scrapers
- Update README if adding a new provider or config option
