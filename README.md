# LicenseLens

[![CI](https://github.com/danielrobinsong143-cmyk/prince28-dev09-licenselens/actions/workflows/ci.yml/badge.svg)](https://github.com/danielrobinsong143-cmyk/prince28-dev09-licenselens/actions/workflows/ci.yml)

[![Live](https://img.shields.io/website?url=https%3A%2F%2Flicenselens-api.onrender.com%2Fhealth&label=live)](https://licenselens-api.onrender.com/docs)

[![Uptime (30 days)](https://img.shields.io/uptimerobot/ratio/m804746153-b82b8cc47bb19bfc51cc1489?label=uptime)](https://licenselens-api.onrender.com/health)


**Live:** https://licenselens-api.onrender.com · [API docs](https://licenselens-api.onrender.com/docs) · [health](https://licenselens-api.onrender.com/health)

Explains open-source licences and checks whether a set of dependencies can be used in a closed-source commercial product.

**Status:** Week 1 complete · live at https://licenselens-api.onrender.com

## Who it's for

CTO of a 10-person Chennai startup who must check 300 open-source dependencies before an investor's due diligence.

## The problem

Checking 300 open-source dependencies manually is difficult and takes a lot of time. Different open-source licences have different rules and conditions, which makes it difficult to understand whether they can be used together in a closed-source commercial product. If a developer misses an important licence obligation, the startup may face legal and compliance risks. LicenseLens helps the CTO identify these risks and understand licence obligations before investor due diligence.

## Data sources

SPDX license list data (licence texts and metadata), choosealicense.com licence metadata, ScanCode licence database.

- https://github.com/spdx/license-list-data
- https://github.com/github/choosealicense.com
- https://github.com/aboutcode-org/scancode-toolkit

Terms: SPDX data CC0; choosealicense MIT; ScanCode data CC-BY-4.0.

## Hardest problem (my interview story)

Compatibility logic: GPL-2.0-only vs GPL-2.0-or-later, LGPL linking, AGPL network use, and dual licences ('MIT OR Apache-2.0') need SPDX expression parsing and a rules table in code, not LLM guesses.

## Signature feature

Dependency verdict board: upload a dependency list and see each package as OK / Obligations / Blocker with the licence clause cited.

## Target metric

Verdict correct for 90%+ of 60 dependency cases checked by a reviewer.

## Run it locally

```bash
uv sync
copy .env.example .env
uv run uvicorn licenselens.api.main:create_app --factory --reload
```

Then open http://localhost:8000/docs

## Checks

```bash
uv run pytest
uv run ruff check .
```

## Target adjustments

The initial record lookup target is 500 ms to provide a practical performance goal for the first version. The answer cost target is Rs 1.00 per question to allow flexibility while evaluating the AI model and retrieval pipeline. Both targets can be tightened after performance and cost measurements.

## Run locally in one command

You need Docker Desktop running. From a fresh clone:

```bash
docker compose up --build
```

This starts Postgres (with pgvector), Redis, runs the migrations and loads the sample data once, then starts the API.

- API docs: http://localhost:8000/docs
- Liveness: http://localhost:8000/health
- Readiness (checks the database): http://localhost:8000/ready

Stop with `Ctrl+C`, then `docker compose down`. Add `-v` to also delete the database volume.

## Develop and test

| Task | Mac / Linux | Windows |
|---|---|---|
| Run the API with reload | `make dev` | `uv run poe dev` |
| Unit tests (no network) | `make test` | `uv run poe test` |
| Integration tests (needs `docker compose up`) | `make test-integration` | `uv run poe test-integration` |
| Coverage report | `make cov` | `uv run poe cov` |
| Lint and format check | `make lint` | `uv run poe lint` |
