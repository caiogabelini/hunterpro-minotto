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
  api/routes/     FastAPI endpoints (health.py, leads.py, auth.py)
  api/deps.py     auth dependencies (get_current_user, require_admin)
  core/           settings (Pydantic BaseSettings from .env) + SQLAlchemy engine/session
                  + security.py (password hashing, JWT)
  models/         SQLAlchemy models (Lead, User)
  schemas/        Pydantic serialization schemas
  scoring/        lead-score rules and niche-specific structured-data sources
  services/       external-data parsers/clients (PGFN + Receita Federal bulk-file
                  parsers, CNES/Receita Federal/IBGE/Evolution/Hunter.io/ZeroBounce/
                  Firecrawl/Anthropic/Google Places HTTP clients)
  workers/        Celery tasks, one per pipeline stage
backend/alembic/  DB migrations
backend/scripts/  one-off CLI scripts (create_user.py — the only way to create a user)
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

Only the PGFN criterion has a **client-confirmed** points formula
(`pgfn_points()`, confirmed 2026-08-19): points scale linearly with active
PGFN debt registrations, capped at 6. The other 8 criteria's formulas live
in a separate file, `app/scoring/compute_lead_score.py` — deliberately not
in `rules.py`, so the confirmed-vs-session-decision boundary is visible
just from which file you're reading. See that file's section below.

### Lead score combination (`app/scoring/compute_lead_score.py`)

`montar_score_lead(enriquecimentos)` is the pure function `compute_lead_score`
(the Celery task) wraps — takes a dict keyed by enrichment task name (e.g.
`"enrich_pgfn_divida_ativa"`, `"enrich_cnes"`, ...) matching each task's
actual return shape in `celery_app.py`, and produces `score_total` (0-100)
+ a per-criterion breakdown (label, weight, points, `SignalLayer`) for the
lead dossier to show "where the points came from." A missing/never-run
key just means "no signal" for that criterion — never raises.

Each of the 8 non-PGFN criteria has a small `_points()` function (same
shape as `pgfn_points`: takes the extracted signal, optional `weight`
override). Most are straightforwardly binary (`zona_franca_alc`,
`whatsapp_ativo`, `email_validado`, `decisor_identificavel`,
`unidade_cnpj_ativo` — full weight or zero). Two are session decisions,
**confirmed by product (Caio Gabelini) on 2026-08-19 — not by the client
(Douglas)**, keep that distinction when reading/writing docs about them:

- `rqe_especialista_points`: combines the AI's `rqe_mencionado` (site
  content) with CNES's `encontrado_cnes` (health-facility registration,
  no specialty field). Graduated by combination — both agree positive →
  full weight; AI-only → 2/3 (a direct RQE mention is stronger evidence
  than CNES presence, which never actually confirms specialty); CNES-only
  → 1/3; neither → 0. `rules.py` and `cnes.py` still note this combination
  was never taken to Douglas explicitly — the product confirmation is
  enough to keep the implementation as-is; revisit only if Douglas asks
  for a different rule.
- `presenca_digital_points`: no objective rule existed for "strong digital
  presence" beyond the source list ("Site + Instagram/LinkedIn"). Decided:
  full weight if both site-with-real-content (`scrape_sucesso` +
  non-empty `site_content_resumo`) and Instagram are present, half weight
  for exactly one, zero for neither. LinkedIn isn't in the signal yet
  (`enrich_linkedin` doesn't exist) — when it does, re-derive the
  graduation rather than bolting on a third "third."

`rating_google_points` exists and is wired into the breakdown even though
the criterion's weight is confirmed at 0 ("pra mim não importa") — the
function's threshold logic (`rating >= 4.0` → full weight) only matters
once `search_google_places` actually returns a rating, which it doesn't
yet (`NotImplementedError`).

**`compute_lead_score` doesn't fetch `enriquecimentos` itself** — it stays
a pure combination function. `process_lead_pipeline` (below) is what
assembles `enriquecimentos` and calls it.

### Pipeline orchestration (`process_lead_pipeline` in `celery_app.py`)

Added 2026-08-20, resolving the gap noted above. Runs the enrichment
stages for one CNPJ **in dependency order, not the fixed ETAPA-1-through-11
list order** — the actual dependency graph is spelled out in a comment
block right before the task:

- `enrich_receita_federal(cnpj)` runs first — it's the source of
  `municipio`/`uf` (→ `enrich_zona_franca`), `codigo_municipio_cnes`
  (→ `enrich_cnes`), and `decisor_nome` (→ `enrich_email`, optional).
- `enrich_pgfn_divida_ativa(cnpj)` is independent, runs anytime.
- `enrich_zona_franca` and `enrich_cnes` are skipped (not attempted) if
  `enrich_receita_federal` failed or didn't find the CNPJ — there's
  nothing to pass them.
- `search_google_places` (real since 2026-08-20, see the "Google Places"
  section above) runs next, searched by whatever name is available
  (explicit `razao_social` param, or `enrich_receita_federal`'s
  `razao_social`/`nome_fantasia`) + UF if known. Skipped only when no
  name is available at all — that's the one case that never even
  attempts the call. Its `site` result becomes `site_url_efetivo`
  when the caller didn't pass `site_url` explicitly (explicit always
  wins) — this is the actual, automatic source for the gap the next
  bullet used to describe as unresolved.
- `enrich_site_firecrawl(site_url)` and `validate_whatsapp(telefone)`
  need `site_url`/`telefone`. `site_url` now usually comes from
  `search_google_places` (previous bullet); `telefone` still has **no
  implemented source anywhere in the pipeline** — `process_lead_pipeline`
  still accepts it as an optional param for a caller who already knows
  it. Without either, the corresponding stage is skipped with a logged
  reason, same treatment as a missing API key.
- `enrich_email(dominio, decisor_nome)`: **`decisor_nome` is optional,
  not a hard dependency** — `enrich_email` already accepts `None` (falls
  back to a domain-only Hunter.io search). Getting this distinction wrong
  is an easy trap: an earlier draft of this task treated it as required
  and a test caught the bug — see the comment block in `celery_app.py`
  for the corrected graph. Only `dominio` (parsed from `site_url_efetivo`
  via `_extrair_dominio`) actually gates the step.
- `enrich_site_firecrawl` is skipped if `site_url_efetivo` ends up empty
  (no Google Places result and none passed explicitly) OR
  `settings.FIRECRAWL_API_KEY` is empty (checked separately, distinct
  log reasons).

**Test hygiene trap, worth knowing before touching this file**:
`test_process_lead_pipeline.py` has an `autouse` fixture
(`_protege_contra_chamada_real_ao_google_places`) that monkeypatches
`search_google_places` to a safe "not found" fake for *every* test in
that file by default — added after a test that mocked everything else
but `search_google_places` ended up making a real, ~21-second call to
Google using the live `GOOGLE_API_KEY` from `.env`. Any test that wants
to exercise the real integration path overrides it explicitly within
the test body (`monkeypatch.setattr(celery_app, "search_google_places", ...)`
after the fixture already ran — last write wins, same `monkeypatch`
instance). If you add a new sibling stage that becomes a real call in
the future, add the same kind of autouse safety net rather than relying
on every test author to remember to mock it.

Each stage runs through `_rodar_etapa()`, which catches any exception,
logs it, and returns `None` — one stage failing never stops the rest of
the pipeline or the final persistence. The task calls sibling task
functions directly (e.g. `enrich_receita_federal(cnpj)`, not `.delay()`)
— Celery task objects are callable and run synchronously in-process when
invoked that way, so no broker is needed to run or test this. Tests
monkeypatch each sibling at the `app.workers.celery_app` module level
(`monkeypatch.setattr(celery_app, "enrich_cnes", fake)`) rather than
injecting them as parameters — simpler and consistent with how the rest
of the module already calls siblings by bare name.

**Persistence** (`_persistir_enriquecimento_lead`) upserts a `Lead` row
by `cnpj` (unique/indexed on the model) — same client-injectable pattern
as the HTTP modules, but for a SQLAlchemy `Session`: `db=None` creates a
real `SessionLocal()` (`_get_db_session`, mirrors `_get_client`), tests
inject an in-memory SQLite session instead (`create_engine("sqlite:///:memory:")`
+ `Base.metadata.create_all(engine)`) — no Postgres needed to test any of
this. Each field group is only overwritten if its source stage actually
ran this round, so a skipped/failed stage never blanks out data persisted
by a previous successful run for the same lead. `razao_social` (`NOT NULL`
on the model) falls back to the `razao_social` param or the bare CNPJ if
`enrich_receita_federal` never succeeds, purely to avoid a constraint
violation — overwritten for real once that stage works. Score fields are
set in a second pass after `compute_lead_score` runs, using the real
`lead.id` from the upsert (not the CNPJ) as `lead_id`.

**What this doesn't do yet**: no task loops over `gerar_semente_leads`'s
output calling `process_lead_pipeline` per CNPJ — that glue is still
manual. No real Celery `chain`/`group` parallelism either; independent
stages (PGFN vs. Receita Federal) just run sequentially in a fixed order
for simplicity/testability, not concurrently.

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

### Site scraping + AI inference (`app/services/site_scraping.py`, `app/services/ai_enrichment.py`)

`site_scraping.py` calls Firecrawl (`POST api.firecrawl.dev/v1/scrape`,
`Authorization: Bearer {FIRECRAWL_API_KEY}`, body `{"url", "formats": ["markdown"]}`,
response `{"success", "data": {"markdown", "metadata"}}`). **Confirmed
against Firecrawl's own `openapi.json` on GitHub — still not against a
live call**, even though `FIRECRAWL_API_KEY` has been in `.env` since
2026-08-20 (see "Network access from Bash is blocked" below for why).
v1-vs-v2 was rechecked on 2026-08-20 as requested: the canonical repo
(`github.com/firecrawl/firecrawl`) only has `v1-openapi.json` — no
`v2-openapi.json` exists there, and even the unversioned `openapi.json`
in that repo is itself `"version": "1.0.0"` with `servers` pointing at
`/v1`. The docs *website* promotes v2, but that's a rendered SPA with no
scrapable examples (same problem as CNES's Swagger UI) — there's no
verifiable v2 contract anywhere, so migrating would mean guessing a
schema, which this project avoids on principle. Staying on v1;
reconsider only if a v2 spec becomes independently verifiable.
`extrair_instagram()` is pure regex (no AI), matching
`instagram\.com/([A-Za-z0-9_.]{2,30})` and discarding non-profile paths
(`p`, `reel`, `reels`, `stories`, `explore`, `accounts`, `share`).

`ai_enrichment.py` calls the Anthropic Messages API via **raw HTTP with
`httpx`, not the `anthropic` SDK** — a deliberate deviation from the
usual "prefer the official SDK" default, made to stay consistent with
every other integration in this codebase (client-injectable, base-URL-less
or base-URL'd `httpx.Client`, `httpx.MockTransport`-tested) and because
that's explicitly the test pattern this project's sessions have asked
for. Model is `claude-haiku-4-5-20251001` (cheapest tier, adequate for
short classification/extraction). The prompt asks for a JSON object
(`rqe_mencionado`, `rqe_numero`, `servicos`) — **not** the API's formal
structured-output feature — because Claude's actual output format varies
enough in practice that `_extrair_json()` needs to try a few fallbacks
(raw parse, ```json fenced block, first `{...}` substring) before giving
up; on total failure it returns empty/null fields, never raises. If a
future session wants schema-guaranteed output, that's `output_config.format`
on the Messages API — a bigger change than this session's scope. Also
still not live-tested despite `ANTHROPIC_API_KEY` being available now —
same environment limitation.

### Google Places (`app/services/google_places.py`)

Implemented 2026-08-20, resolving `search_google_places`'s
`NotImplementedError`. Uses **Places API (New)** Text Search only
(`POST places.googleapis.com/v1/places:searchText`, headers
`X-Goog-Api-Key` + `X-Goog-FieldMask`, no default field list — you must
name every field you want) — **not** the legacy Places API, and **not**
a separate Place Details call: confirmed against Google's own docs that
Text Search (New) already returns `websiteUri`/`rating`/`userRatingCount`
directly when they're in the field mask, so one call per establishment
suffices (also cheaper — those three fields are the "Enterprise" SKU
tier). Contract confirmed against `developers.google.com/maps/documentation/places/web-service/{text-search,place-details}`,
**not against a live call** — same environment limitation as
Firecrawl/Anthropic (see below), plus the legacy API's key-in-URL
alternative was explicitly declined by the user after an accidental key
exposure earlier in this session (see below).

`search_google_places` (the Celery task) takes the seed's list shape
(`cnpj`/`razao_social`/`nome_fantasia`/`uf` dicts) and returns the same
list with `google_place_id`/`site`/`rating`/`avaliacoes`/`telefone_google`
merged in — a per-item HTTP failure is caught and logged, never takes
down the whole batch. `process_lead_pipeline` calls it with a one-item
list for its single CNPJ and unwraps `[0]`; the returned `site` becomes
the automatic `site_url` source for `enrich_site_firecrawl`/`enrich_email`
when the caller didn't pass one explicitly (explicit `site_url` still
wins) — this is what actually closes the "no automatic `site_url` source"
gap noted in earlier sessions, now that this stage is real.

### Network access from Bash is blocked in this sandbox

Confirmed 2026-08-20: `curl` (or any outbound network command) via the
Bash tool is denied by this environment's permission policy — tested and
denied even for a bare `curl https://www.google.com`. `WebFetch` still
works but only does simple GET with no custom headers and no POST body,
so it cannot authenticate against any API that needs `POST` + a header
(Firecrawl, Anthropic Messages, Places API New) — only GET-with-key-in-
query-string APIs (like the *legacy* Places API) are technically
reachable through it. Curiously, outbound HTTPS from `httpx` running
*inside* a Python process launched via Bash did seem to reach the
network in one accidental case (a test that forgot to mock
`search_google_places` took ~21s instead of ~5s) — this was never
deliberately verified or exploited, since the user had already declined
live validation by the time it was noticed, but it's worth someone
checking directly in a future session before assuming curl-level
blocking applies there too.

**Incident, for context:** while probing whether the legacy
(GET+query-string) Places API was viable, a shell command that
constructed the search URL was run in a way that printed the full URL —
**including the live `GOOGLE_API_KEY`** — into the visible conversation
output. The user was told immediately; they chose not to expose it
further (declined the GET-based workaround for the same reason) and did
not ask for a rotation on the spot, but flagged it as something to
consider. **Lesson: never build a URL containing a secret and pass it to
any tool whose invocation or output is visible** — not `Bash` echoing a
`curl`, not a printed `python -c` result, and not `WebFetch`'s `url`
parameter either, since that's just as visible. If a query-string-
authenticated API must be exercised for real, get the user's explicit
go-ahead first (as eventually happened here, after the fact) — don't
assume "it's just their own conversation" makes it fine by default.

### Celery pipeline (`app/workers/celery_app.py`)

As of 2026-08-19 the pipeline no longer starts with a free-form Google
Places search — it starts with `gerar_semente_leads` (ETAPA 0, not part of
the per-lead flow below), which filters CNPJs from the RFB bulk files and
joins in razão social. Tasks per lead: `search_google_places` (real
since 2026-08-20 — an *enrichment* step over the already-known CNPJs
from the seed, one Text Search per CNPJ by `nome_busca_google_places()`'s
pick, razão social preferred over nome_fantasia; see the "Google Places"
section above — not live-tested, but no longer a stub) → Receita
Federal → PGFN → Zona Franca → CNES → `enrich_site_firecrawl` (also
covers what would've been a separate `enrich_instagram` step — the
Instagram link comes out of the same scrape, no extra task needed) →
LinkedIn → `validate_whatsapp` → `enrich_email` → `compute_lead_score`
(see the "Lead score combination" section above). As of 2026-08-20,
`process_lead_pipeline` (see "Pipeline orchestration" section above) is
the actual entry point for running this whole sequence for one CNPJ —
it calls the individual stage functions itself, in real dependency
order, not the fixed ETAPA list order shown here (that list is the
kickoff-doc reference ordering, not an execution contract).
Implemented so far: `gerar_semente_leads`, `search_google_places`,
`enrich_receita_federal`, `enrich_zona_franca`, `enrich_pgfn_divida_ativa`
(plus its prerequisite `refresh_pgfn_index`), `enrich_cnes`,
`enrich_site_firecrawl`, `validate_whatsapp`, `enrich_email`,
`compute_lead_score`, `process_lead_pipeline` — every task in the file
now has a real implementation. `enrich_linkedin` doesn't exist as a task
at all yet (never even got a placeholder), unlike every other pipeline
stage; it needs to be created from scratch, not just filled in, when
picked up (and then wired into `process_lead_pipeline` too).

### `.env.example` is inaccessible in this sandbox

Reads and writes to any `backend/.env*` file (including `.env.example`)
are blocked by this environment's permission settings — confirmed
repeatedly across sessions, not a one-off. When a new `Settings` field is
added to `config.py` that needs a real value (e.g. `FIRECRAWL_API_KEY`,
added 2026-08-19), it won't be reflected in `.env.example` — check
`config.py` directly for the full settings surface, and flag the gap to
the user rather than silently skipping it.

### Authentication (`app/models/user.py`, `app/core/security.py`, `app/api/routes/auth.py`, `app/api/deps.py`)

Added 2026-08-20, deliberately minimal — see README's "O que falta" item
11 for the explicit list of what was left out on purpose (refresh
tokens, password recovery, public registration) rather than forgotten.

- `User` (`app/models/user.py`): `id` (uuid string, same pattern as
  `Lead`), `email` (unique/indexed), `senha_hash`, `role` (plain
  `String(10)` storing a `UserRole(str, Enum)`'s `.value` — `"admin"` |
  `"client"` — not a native DB enum type, same pragmatic-string
  convention as `Lead.rqe_fonte`/`Lead.prioridade`, avoids the extra
  Alembic ceremony a native Postgres ENUM needs on both upgrade and
  downgrade), `ativo` (bool, default `True`), `created_at`.
- `app/core/security.py`: `hash_password`/`verify_password` (passlib
  bcrypt) and `create_access_token`/`decode_access_token` (python-jose,
  HS256, `settings.SECRET_KEY` + `settings.ACCESS_TOKEN_EXPIRE_MINUTES`).
  `decode_access_token` never raises — catches `JWTError` (the base
  class covering both bad-signature and expired-token cases) and
  returns `None`, so callers only ever check for `None`.
- **Real bug hit and fixed**: `passlib==1.7.4` (already pinned) is
  broken with `bcrypt>=4.0` — passlib's backend-detection code reads
  `bcrypt.__about__.__version__`, an attribute removed in bcrypt 4.x.
  The failure mode is *not* a clear compatibility error — it's a
  misleading `ValueError: password cannot be longer than 72 bytes`,
  even for a 12-character password, because passlib's internal
  self-test silently breaks first. Confirmed by hitting this for real
  in this session (bcrypt resolved to 4.0.1 by default, since
  `passlib[bcrypt]==1.7.4` doesn't pin the bcrypt version itself). Fixed
  by pinning `bcrypt==3.2.2` explicitly in `requirements.txt` — don't
  bump bcrypt without re-validating `hash_password`/`verify_password`
  against the new version first.
- `POST /api/auth/login` (`app/api/routes/auth.py`, mounted at
  `/api/auth`): plain JSON body (`email`, `senha` — not OAuth2
  form-encoded `username`/`password`, since there's no OAuth2 flow
  here), returns `{"access_token", "token_type": "bearer"}` with
  `user_id` and `role` in the JWT payload. One generic 401 for
  nonexistent email, wrong password, *and* inactive user — deliberately
  not distinguished, so a failed login never reveals which emails have
  accounts.
- `app/api/deps.py`: `get_current_user` uses `HTTPBearer(auto_error=False)`
  (not the default `auto_error=True`) specifically so a missing
  `Authorization` header can be folded into the same 401 path as an
  invalid/expired token, instead of `HTTPBearer`'s own default of 403
  for "no credentials at all" — keeps "who are you" (401) cleanly
  separate from "I know who you are, but no" (403, `require_admin`).
  `get_current_user` also re-checks `ativo` on every request (not just
  at login) — a deactivated user's still-valid token stops working
  immediately, not just at next login. `require_admin` is wired and
  tested (`tests/test_deps.py`, called directly with constructed `User`
  objects — no admin-only route exists yet to exercise it through HTTP)
  but **doesn't protect anything yet** — no admin-only route has been
  built. `GET /api/leads` requires only `get_current_user` (any
  authenticated role), not `require_admin`.
- `scripts/create_user.py`: CLI (argparse: `--email`, `--senha`,
  `--role`) for creating users — the only way to create one, since
  there's no public registration endpoint by design (only Minotto and
  4Hands use the system). Refuses to run if the email already exists.

**Test infra gotcha worth knowing**: `tests/test_auth_routes.py` uses
`fastapi.testclient.TestClient` against an in-memory SQLite DB, and
initially hit a classic trap — `TestClient` runs the endpoint in a
*different thread* (via `anyio.to_thread.run_sync`) than the one that
built the DB fixture and ran `Base.metadata.create_all()`. Plain
`sqlite:///:memory:` gives each thread its own separate, empty
in-memory database (`SingletonThreadPool` is the sqlite3 dialect's
default for `:memory:` URLs) — symptom was `no such table: users` in
some tests but not others, depending on whether a given test's fixture
happened to force a query in the main thread first. Fix: pass
`poolclass=StaticPool` (from `sqlalchemy.pool`) alongside
`connect_args={"check_same_thread": False}` — pins the engine to one
shared connection across every thread. Apply the same fix to any future
test that spins up a `TestClient` against SQLite; `test_process_lead_pipeline.py`
never needed this because it calls task functions directly, never
through `TestClient`'s threadpool.

### Migrations (`backend/alembic/`)

**`alembic/versions/` was empty before 2026-08-20** — there was no
baseline migration for the `leads` table (`app/models/lead.py`), even
though that model and its API route have existed since the project's
first commit. The new `6102ef9fc509_cria_tabela_users.py` migration
(for `User`, see above) is the *first* migration in this repo, written
by hand — no Postgres was reachable with the `.env` credentials in this
sandbox (auth failure even using the real configured `DATABASE_URL`),
so `alembic revision --autogenerate` had nothing to diff against.
Consequence: `alembic upgrade head` on a genuinely empty database
creates `users` but **not** `leads` — whatever mechanism has been
creating the `leads` table until now (manual `Base.metadata.create_all()`,
most likely) still needs to keep doing so until someone writes that
baseline migration too. Not this session's scope to fix, just flagged
so it doesn't surprise anyone running migrations against a fresh DB.
`alembic/env.py` now imports both `app.models.lead` and `app.models.user`
so a future `--autogenerate` run (once a live DB is reachable) picks up
both.

### Deployment note (EasyPanel)

Backend, Celery worker, and frontend are deployed as **separate App
Services** — Docker Compose multi-service setup is not well supported on
EasyPanel (lesson carried over from the SR Contábil project). The frontend's
nginx must point at the backend's public domain, not an internal Docker
hostname.
