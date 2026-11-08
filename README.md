# Awning
Every shop has an awning. Now AI agents can see it.

## Run locally

```
uv sync
cp .env.example .env   # then fill in the values
uv run uvicorn awning.main:app
uv run pytest
```
