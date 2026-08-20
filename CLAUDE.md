# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

HunterPro — Minotto Contabilidade: a lead-prospecting engine built by 4Hands AI for
Douglas Minotto (Minotto Contabilidade), targeting medical clinics/practices
(any specialty with RQE) nationwide in Brazil. It follows the same architecture
already validated in a prior sibling project (HunterPro SR Contábil).

The pipeline (search → enrich → score) is built incrementally, session by
session. Most enrichment steps in `app/workers/celery_app.py` are still
`NotImplementedError` placeholders — check `README.md`'s "O que já está
pronto" / "O que falta" sections for current status before assuming a
feature exists.

Code, comments, and commit messages in this repo are in Portuguese (Brazil).
Match that when editing existing files.

## Commands

### Backend (FastAPI + PostgreSQL + Celery + Redis)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in API keys / DB & Redis URLs

uvicorn app.main:app --reload   # run the API (needs Postgres + Redis reachable)
celery -A app.workers.celery_app worker --loglevel=info   # run the worker

pytest tests/ -v                # run all tests
pytest tests/test_pgfn_formula.py -v          # single file
pytest tests/test_pgfn_formula.py::test_name -v   # single test

alembic revision --autogenerate -m "message"  # new migration
alembic upgrade head                          # apply migrations
```

There is no pytest.ini/pyproject.toml config — tests run with plain `pytest`
defaults from the `backend/` directory. Redis-dependent tests (e.g.
`test_pgfn_cache.py`) use an in-memory fake client (`FakeRedis`), not a real
Redis — no service needs to be running to run the suite.

### Frontend (React 19 + Vite + TypeScript)

```bash
cd frontend
npm install
cp .env.example .env
npm run dev       # dev server
npm run build     # tsc -b && vite build
npm run lint       # oxlint
npm run preview
```

No test runner is configured for the frontend yet.

## Architecture

```
backend/app/
  api/routes/     FastAPI endpoints (health.py, leads.py)
  core/           settings (Pydantic BaseSettings from .env) + SQLAlchemy engine/session
  models/         SQLAlchemy models (Lead)
  schemas/        Pydantic serialization schemas
  scoring/        lead-score rules and niche-specific structured-data sources
  services/       external-data parsers/clients (PGFN + Receita Federal bulk-file
                  parsers, CNES/Receita Federal/IBGE/Evolution/Hunter.io/ZeroBounce
                  HTTP clients)
  workers/        Celery tasks, one per pipeline stage
backend/alembic/  DB migrations
backend/tests/    pytest suite, one file per module under test

frontend/src/
  api.ts          typed HTTP client to the backend (health check + leads fetch)
  App.tsx         placeholder leads table UI
```

### Lead scoring engine (`app/scoring/rules.py`)

`SCORING_CRITERIA` is a list of 9 `ScoringCriterion` entries whose weights
were closed with the client on 2026-08-10 and **must sum to exactly 100** —
a module-level `assert` enforces this at import time. Each criterion also
carries a `SignalLayer` (`ESTRUTURADO` / `INFERENCIA` / `VALIDACAO`)
indicating source reliability, using the same vocabulary from the client
kickoff deck — preserve it if you touch this file.

Do not change a criterion's `weight` without client sign-off; if you do,
every other weight must be rebalanced so the total stays 100 (see the
`divida_ativa_pgfn` note in the file for a past example of this trade-off).

Only the PGFN criterion currently has a confirmed points formula
(`pgfn_points()`, confirmed with the client 2026-08-19): points scale
linearly with active PGFN debt registrations, capped at 6. The other 8
criteria still need their own point-conversion formulas before
`compute_lead_score` (currently a `NotImplementedError` placeholder in
`celery_app.py`) can sum them into `Lead.score_total`.

### PGFN debt data (`app/services/pgfn.py`)

Parses PGFN (Dívida Ativa da União) open-data CSVs — quarterly, downloaded
manually into `settings.PGFN_DADOS_ABERTOS_DIR` (no automated download yet).
Two-stage design, mirrored in the Celery tasks:

- `build_pgfn_index` / `refresh_pgfn_index` task — heavy, streams the CSV
  (never loads it fully into memory — it can be millions of rows), filters
  to pessoa jurídica + still-active registrations, and writes one Redis key
  per CNPJ. Runs only when the quarterly file updates, not per lead.
- `lookup_debt_from_redis` / `enrich_pgfn_divida_ativa` task — cheap
  per-lead Redis lookup against the index built above.

`PGFN_COLUMNS`, `DEFAULT_ENCODING`, and `DEFAULT_DELIMITER` at the top of
the file are best-effort guesses based on public documentation, not a
verified schema — if a real downloaded CSV has different column names or
encoding, adjust these constants rather than changing parsing logic
elsewhere in the module.

### CNES data (`app/services/cnes.py`)

Live HTTP client (not a downloaded file, unlike PGFN) for
`apidadosabertos.saude.gov.br/cnes/estabelecimentos`. Its real behavior was
confirmed by hitting the endpoint directly (2026-08-19) since the public
docs are a JS-rendered Swagger SPA that doesn't scrape:

- The API filters by `codigo_municipio` (6-digit IBGE code, no check
  digit) but silently **ignores** any CNPJ query param — so CNPJ matching
  (`find_estabelecimento_by_cnpj`) happens client-side, after fetching all
  establishments for a município.
- The server caps page size well below whatever `limit` is requested
  (observed 20 regardless of asking for 500) — `fetch_estabelecimentos_por_municipio`
  paginates by advancing `offset` by the actual page size received, not a
  hardcoded constant, stopping on the first empty page.
- The establishment payload has no specialty/RQE field at all — CNES
  presence is only a structural reinforcement signal for the
  `rqe_especialista` criterion, not a standalone confirmation.

`enrich_cnes` (the Celery task) needs `codigo_municipio` passed in — resolved
by `enrich_receita_federal` (see below), which now runs first in the pipeline.

### Receita Federal data (`app/services/receita_federal.py`)

Live HTTP client for BrasilAPI (`brasilapi.com.br/api/cnpj/v1/{cnpj}`), a
free, unauthenticated mirror of the Receita Federal's own CNPJ open data.
Same per-CNPJ, no-bulk-file design as CNES. Two things worth knowing:

- `identificar_decisor` picks the likely decision-maker from the sócios
  (QSA) list by keyword-matching the `qualificacao_socio` string (e.g.
  "ADMINISTRADOR", "PRESIDENTE") — there's no verified official
  qualification-code table to match against instead, so don't replace this
  with a numeric-code lookup without first confirming the codes against
  Receita Federal's own documentation.
- The response includes `codigo_municipio_ibge` (7-digit IBGE code)
  directly per CNPJ — this is what resolves CNES's `codigo_municipio` gap
  (see `app/services/ibge_municipios.py::codigo_ibge_para_cnes` below),
  no separate name-based lookup needed once a lead has gone through this
  step.

### Município → IBGE/CNES code (`app/services/ibge_municipios.py`)

Resolves município name + UF to an IBGE code by querying IBGE's own public
Localidades API live, rather than embedding a static table — deliberately
different from `zona_franca.py`'s pattern. With ~5,570 municípios
nationwide, a hand-typed/memorized table risks a silent wrong-digit error
that would only surface as `enrich_cnes` mysteriously returning no results
for one município; the small Zona Franca list (10 entries) is checkable
against the Suframa page, this one isn't practical to eyeball. In practice
this module is a fallback — `enrich_receita_federal` already returns
`codigo_municipio_ibge` directly, avoiding the name-matching step entirely.

`codigo_ibge_para_cnes()` does the IBGE-7-digit → CNES-6-digit conversion
(drop the trailing check digit) — confirmed empirically against the live
CNES API on 2026-08-19 (São Paulo: 3550308 → 355030), not from documentation.

### Lead seed from the Receita Federal bulk file (`app/services/receita_federal_bulk.py`)

Parses the RFB's "Estabelecimentos" open-data file (monthly, split across
several files like `K3241.K03200Y0.D60411.ESTABELE` — the number is just a
shard, layout is identical across shards) — 30 fixed-position columns,
**no header row**, semicolon-delimited, quoted fields. Layout was confirmed
against the RFB's own metadata PDF (`gov.br/receitafederal/dados/cnpj-metadados.pdf`)
on 2026-08-19, not guessed — see `ESTABELECIMENTOS_COLUNAS` for the exact
order. Same streaming/no-bulk-load design as `pgfn.py`.

Filters on CNAE prefix `8630` (grupo "Atividade médica ambulatorial") minus
subclass `8630504` (Atividade odontológica — excluded because the niche is
RQE-holding physicians, and dentists don't hold RQE), plus UF/região and
active-status by default.

**Razão social comes from a second bulk file, joined in.** ESTABELECIMENTOS
only has `nome_fantasia` (often blank for small companies) — razão social
(mandatory field) lives in the separate EMPRESAS file
(`K3241.K03200Y0.D10612.EMPRECSV` and shards, layout also confirmed against
the same RFB PDF, 7 columns, same no-header/`;`/quoted convention).
`enriquecer_com_razao_social` joins on `cnpj_basico` (first 8 digits) —
it only streams EMPRESAS looking for the `cnpj_basico` set already
surviving the ESTABELECIMENTOS filter (`build_razao_social_index`), never
indexes the full ~60M-row file. A `cnpj_basico` missing from the EMPRESAS
file(s) passed in just leaves `razao_social` as `""` (never `None`,
never raises) — expected in tests with a partial EMPRESAS fixture, not
expected in production with the real same-month files. `razao_social` is
the primary field on the `gerar_semente_leads` output; `nome_fantasia` is
the fallback, chosen by `nome_busca_google_places()` in `celery_app.py`.

**`municipio` here is not an IBGE code** — it's an RFB-internal
jurisdiction code from RFB's own domain table, unrelated to IBGE codes.
Don't feed it to `ibge_municipios.py` or CNES; `enrich_receita_federal`'s
`codigo_municipio_ibge` (from BrasilAPI) is still the only confirmed path
to that code.

`gerar_semente_leads` (the Celery task) wraps both files' parsing the same
way `refresh_pgfn_index` wraps `pgfn.py` — reads from
`settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR` (manual download, both file
types land in the same folder since RFB ships them together monthly), and
is not a per-lead task.

### WhatsApp validation (`app/services/whatsapp.py`)

Live HTTP client for Evolution API
(`POST {EVOLUTION_URL}/chat/whatsappNumbers/{EVOLUTION_INSTANCE}`, header
`apikey`) — endpoint and contract already validated in production on the
SR Contábil sibling project, implemented here to match exactly.

`formatar_numero_whatsapp()` decides whether to prepend the country code
(55) by the cleaned number's **length** (10/11 digits = national number,
always prepend), not by checking whether it already starts with "55" —
DDD 55 is Rio Grande do Sul, so a bare 11-digit local number can start
with "55" without having the country code at all. Prefix-checking would
silently under-prefix those numbers. Only calls the API if the final
result is 12-13 digits; otherwise returns `numero_valido=False` without
an HTTP call.

### Email enrichment (`app/services/email_enrichment.py`)

Three chained steps, contract for both APIs already validated in
production on SR Contábil:

1. Hunter.io Email Finder (`GET api.hunter.io/v2/email-finder`, params
   `domain`/`first_name`/`last_name`/`api_key`) — `_dividir_nome()` splits
   whatever single `nome_decisor` string the pipeline has into first/last
   name. A 200 response with `data.email: null` is a valid "not found"
   result, not an error.
2. MX check via dnspython (`dns.resolver.resolve(dominio, "MX")`) — cheap
   gate before spending a paid ZeroBounce lookup on a domain that can't
   receive mail. The `resolver` param is injectable (same DI pattern as
   the HTTP `client` elsewhere) so tests don't touch real DNS.
3. ZeroBounce (`GET api.zerobounce.net/v2/validate`) — only `valid` and
   `catch-all` count as approved (`ZEROBOUNCE_STATUS_APROVADOS`); `invalid`,
   `do_not_mail`, `abuse` don't.

`enriquecer_email()` short-circuits after step 1 or 2 if they fail, never
reaching the paid ZeroBounce call unnecessarily. Unlike every other HTTP
module in this project, it does **not** set `client.base_url` — Hunter.io
and ZeroBounce are different hosts, so each function passes an absolute
URL and a single client (real or injected) serves both.

### Celery pipeline (`app/workers/celery_app.py`)

As of 2026-08-19 the pipeline no longer starts with a free-form Google
Places search — it starts with `gerar_semente_leads` (ETAPA 0, not part of
the per-lead flow below), which filters CNPJs from the RFB bulk files and
joins in razão social. Tasks per lead: `search_google_places` (now an
*enrichment* step over the already-known CNPJs from the seed — searches
Google Places by `nome_busca_google_places()`'s pick, razão social
preferred over nome_fantasia; the actual Places API call is still
`NotImplementedError`, blocked on a real `GOOGLE_API_KEY` to validate
against, unlike the free/no-auth CNES/IBGE/BrasilAPI sources) → Receita
Federal → PGFN → Zona Franca → CNES → site/Firecrawl → Instagram →
LinkedIn → `validate_whatsapp` → `enrich_email` → `compute_lead_score`.
Implemented so far: `gerar_semente_leads`, `enrich_receita_federal`,
`enrich_zona_franca`, `enrich_pgfn_divida_ativa` (plus its prerequisite
`refresh_pgfn_index`), `enrich_cnes`, `validate_whatsapp`, `enrich_email`.
Everything else raises `NotImplementedError` — implement in the same
task-per-pipeline-stage shape when picking up the next stage.

### Deployment note (EasyPanel)

Backend, Celery worker, and frontend are deployed as **separate App
Services** — Docker Compose multi-service setup is not well supported on
EasyPanel (lesson carried over from the SR Contábil project). The frontend's
nginx must point at the backend's public domain, not an internal Docker
hostname.
