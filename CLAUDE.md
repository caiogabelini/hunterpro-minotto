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
npm run test       # vitest run -- pure-function unit tests only, no DOM/component rendering yet
```

`vitest` was added 2026-08-20 (bugfix session, see "Lead dossier score
breakdown bug" below) — deliberately minimal: it only runs plain
TypeScript unit tests against pure functions (`src/leadScore.test.ts` is
the only spec so far). No `jsdom`/React Testing Library, no
component-rendering tests — that's a bigger addition for whenever the
frontend actually needs it, not added preemptively here.

## Architecture

```
backend/app/
  api/routes/     FastAPI endpoints (health.py, leads.py, auth.py)
  api/deps.py     auth dependencies (get_current_user, require_admin)
  core/           settings (Pydantic BaseSettings from .env) + SQLAlchemy engine/session
                  + security.py (password hashing, JWT)
  models/         SQLAlchemy models (Lead, LeadMessage, User)
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
  api.ts               typed HTTP client to the backend (health, login, leads, lead detail, PATCH status, mensagens)
  tokens.css            shadow/transition/spacing/gradient design tokens — see "Visual polish pass" below
  index.css              color palette (navy/gold — dark theme, deliberate, don't revert) + shared badges
  context/AuthContext.tsx  JWT held only in memory (React Context) — see "Frontend: Login + Kanban" below
  components/          Sidebar, AuthenticatedLayout, ProtectedRoute, KanbanColumn, KanbanCard,
                       LossReasonModal, PriorityBadge
  pages/                LoginPage, KanbanPage, LeadDossierPage (+ co-located .css)
  kanbanStatuses.ts     column order/labels, mirrors app/models/lead.py::KanbanStatus
  scoreLayers.ts         SignalLayer label lookup, mirrors app/scoring/rules.py::SignalLayer
  App.tsx               React Router root (/login public, no sidebar; / and /leads/:id
                        protected, wrapped in AuthenticatedLayout for the sidebar)
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

### Kanban (`app/models/lead.py::KanbanStatus`, `PATCH /api/leads/{id}/status`)

Added 2026-08-20, alongside the first frontend screen (see below). `Lead`
gained two columns: `kanban_status` (plain `String(20)`, default
`KanbanStatus.NOVO_LEAD.value` — same string-enum convention as
`User.role`/`Lead.prioridade`) and `motivo_perda` (`Text`, nullable,
only meaningful when `kanban_status == "perdido"`). `KanbanStatus` is a
9-value `str, Enum` in `app/models/lead.py` (not a separate file) —
funnel order: `novo_lead → qualificacao → contatado → respondeu →
reuniao → proposta_enviada → negociacao → ganho → perdido`.

`PATCH /api/leads/{lead_id}/status` (`app/api/routes/leads.py`,
`get_current_user`, any role — matches `GET /api/leads`'s protection
level, not admin-only) validates `kanban_status` against
`KanbanStatus`'s values (422 if not a member) and requires
`motivo_perda` non-empty when the target is `"perdido"` (422 if
missing) — both checks happen in the route, not at the DB/model level,
since the column itself stays nullable (only meaningful in one status).
Moving a lead *out* of `"perdido"` clears `motivo_perda` back to `None`
(the route always sets it to `dados.motivo_perda if target ==
PERDIDO else None`, never leaves a stale reason from a previous loss).
`GET /api/leads` gained an optional `kanban_status` query filter,
same pattern as `prioridade`/`zona_franca`. Tests in
`tests/test_leads_routes.py` (9 new).

`GET /api/leads/{lead_id}` (added 2026-08-20, alongside the lead dossier
screen — see below) is a separate route from both of the above: single-
lead detail, same `get_current_user` protection. Added because
`LeadRead` — and by extension `list_leads`'s response — didn't expose
most of the model's fields (`cnae_principal`, `anos_mercado`,
`simples_nacional`, `situacao_cadastral`, `telefone`, `site`,
`instagram`, `linkedin_empresa`, `linkedin_decisor`, `google_rating`,
`google_avaliacoes`, `servicos`) — the Kanban board never needed them,
but the dossier screen does, so `LeadRead` was expanded to include all
of them rather than adding a second, dossier-specific schema.

Migration `a3f7c1d92b4e_adiciona_kanban_status_e_motivo_perda.py`
chains after `6102ef9fc509` (users, the head at the time) — also
hand-written, same reason as the other two (`settings.DATABASE_URL`
still unreachable). Unlike the `leads`/`users` baseline migrations,
**this one has not been validated against any real Postgres** (not even
a throwaway cluster) — only `alembic`'s own upgrade/downgrade logic was
exercised via the SQLite-backed pytest suite, which doesn't run Alembic
at all. If a future session wants that extra validation, repeat the
throwaway-cluster technique described in "Migrations" below.
`kanban_status` uses `server_default="novo_lead"` (not just the
model-level Python default) specifically so `upgrade()` backfills
existing rows instead of leaving them `NULL` on a `NOT NULL` column.

### Frontend: Login + Kanban (`frontend/src/`)

Added 2026-08-20 — the **first** frontend screen, built deliberately
scoped to just these two (see README "O que falta" item 12 for what was
left out on purpose: CSV/Excel export, lead detail/dossier screen,
user registration UI). Screens are being built one at a time, each
validated before the next — don't preemptively build screens beyond
what a given session asks for, even if it looks fast.

- **Routing**: `react-router-dom`, `App.tsx` is just the router root —
  `/login` (public, `LoginPage`) and `/` (`KanbanPage`, wrapped in
  `ProtectedRoute`). Any unmatched path redirects to `/`.
- **Auth token storage — deliberately in-memory only**
  (`context/AuthContext.tsx`, plain `useState` inside a `React.Context`
  provider): **not** `localStorage`, `sessionStorage`, or a cookie, by
  explicit user instruction ("evitar problema de segurança básico" — a
  script running on the page via XSS can't read a token that isn't in
  any persistent browser storage). Trade-off, worth knowing: a page
  reload logs the user out (no token survives it) — accepted
  deliberately for this session, not a bug. `ProtectedRoute` reads
  `useAuth().isAuthenticated` and redirects to `/login` if there's no
  token; it does not attempt to refresh or restore a session on reload.
- **Kanban board** (`pages/KanbanPage.tsx` + `kanbanStatuses.ts`): 9
  fixed columns, hardcoded order (mirrors `KanbanStatus` — if a value is
  ever added to the backend enum, add it to `kanbanStatuses.ts` too,
  nothing keeps them in sync automatically). Cards
  (`components/KanbanCard.tsx`) show razão social, a colored A/B/C
  priority badge (`components/PriorityBadge.tsx`), and `score_total`.
  Dragging into a column other than "Perdido" applies the move
  optimistically (local state updates immediately, `PATCH` fires in the
  background, rolled back on failure); dragging into "Perdido" instead
  opens `components/LossReasonModal.tsx` and only calls `PATCH` (with
  `motivo_perda`) on confirm — cancelling leaves the card in its
  original column, since local state was never touched before confirm.
  A `401` from any Kanban API call (`UnauthorizedError` in `api.ts`)
  calls `logout()` — the next render falls through `ProtectedRoute` to
  `/login`, no explicit `navigate()` call needed for that path.
- **Drag-and-drop: `@dnd-kit/core` + `@dnd-kit/utilities`** — chosen
  over `react-beautiful-dnd` (archived/unmaintained since Atlassian
  dropped it in 2023), `react-dnd` (more verbose low-level API, manual
  HTML5 backend setup, overkill for 9 fixed columns with no
  within-column reordering), and native HTML5 drag-and-drop (weak
  mobile/touch support — a real constraint for a commercial tool that
  might run on a tablet). Full justification is in the README, not
  duplicated here.
- **Visual identity** (`src/index.css`, palette + font custom
  properties consumed by every page/component CSS file): navy
  `#292C5D`/`#1B1F4A` (dark variant), background `#0B0D22`, gold
  `#F5C842`/`#C9A227` (dim variant), Bebas Neue (`--font-display`,
  headings) + Inter (`--font-body`, everything else) loaded via Google
  Fonts `<link>` tags in `index.html` — matches the client's own
  presentation-deck identity (referenced by the user, not itself a file
  in this repo). The old placeholder `App.css` (leads-table demo UI)
  was deleted — superseded by the Kanban screen, nothing imports it
  anymore.
- **Not tested in an actual browser** — this sandbox has no
  network/browser access to click through login → drag-and-drop
  manually (same limitation already documented for Google
  Places/Firecrawl/Anthropic). Validated instead via `tsc -b` (clean
  compile), `npm run build` (clean production build), `npm run lint`
  (oxlint, one harmless `react/only-export-components` warning on
  `AuthContext.tsx` for co-locating the `useAuth` hook with the
  provider — accepted, common pattern), and code review of the
  drag/rollback/modal logic. If a future session has real browser
  access, exercising the actual login → drag → drop-into-Perdido flow
  is the natural next validation step before trusting this UI in
  front of the client.

### Frontend: Lead dossier (`frontend/src/pages/LeadDossierPage.tsx`)

Added 2026-08-20 — the **second** frontend screen, same one-at-a-time,
validate-before-next approach as the Kanban screen (see above). Scoped
deliberately to read-only display; see README "O que falta" item 13 for
what was left out on purpose: editing lead data from this screen, a
status-change history/audit log, CSV/Excel export.

- **Route**: `/leads/:id`, wrapped in the same `ProtectedRoute` as `/`.
  `KanbanCard` (`components/KanbanCard.tsx`) got an `onClick` that calls
  `navigate(`/leads/${lead.id}`)` — this coexists with the card's
  `@dnd-kit` drag handlers without extra guarding because `PointerSensor`
  in `KanbanPage.tsx` already has `activationConstraint: { distance: 6 }`;
  a plain click (no pointer movement past that threshold) never triggers
  drag-start, so the browser's normal click event still fires on the
  same element.
- **Data**: `fetchLead(token, id)` in `api.ts` hits the new `GET
  /api/leads/{lead_id}` (see "Kanban" section above for why `LeadRead`
  needed expanding first). `Lead` in `api.ts` grew a matching
  `ScoreDetalhes`/`ScoreBreakdownItem` pair of interfaces for
  `score_detalhes.breakdown` — the shape `compute_lead_score`
  (`app/workers/celery_app.py`) actually serializes (`key`, `label`,
  `weight`, `layer`, `points` per criterion), not a generic `dict`.
- **Layout**: header (razão social/nome fantasia/CNPJ/priority
  badge/`score_total`) → a dedicated, visually-flagged PGFN section
  (green "no pendency" vs. red with the inscription count — **never**
  blank, per explicit instruction: absence of debt is still information
  worth stating) → a 3-column responsive grid (dados cadastrais /
  contato / presença digital, each a `<dl>` of label-value pairs, with a
  small `CampoIndicador` helper for the two boolean-with-unknown-state
  fields — WhatsApp ativo, e-mail validado) → the score breakdown, one
  row per `SCORING_CRITERIA` entry showing `points/weight pts` and a
  colored left-border tag for `SignalLayer` (green=estruturado,
  gold=inferência, blue=validação — `scoreLayers.ts` holds the
  label lookup, mirroring `app/scoring/rules.py::SignalLayer`'s three
  values verbatim, not a new vocabulary).
- **Shared styling**: `PriorityBadge`'s CSS (`.priority-badge`,
  `.priority-a/b/c`) moved from `KanbanPage.css` to the global
  `index.css` in this session, since the component is now used from two
  pages — if a third page ever reuses it, no further move needed.
- **Not tested in an actual browser** — same standing sandbox limitation
  as the Kanban screen (see above); validated via `tsc -b`, `npm run
  build`, `npm run lint` (only the same pre-existing `AuthContext.tsx`
  fast-refresh warning), and code review.

### Lead dossier score breakdown bug (fixed 2026-08-20)

User report: the dossier showed "Score ainda não calculado pra este
lead" for a lead whose `score_detalhes` was confirmed populated in the
database (direct SQL). Investigation walked the full chain end to end —
worth recording since every layer checked out individually, and the fix
ended up being about robustness, not a specific broken line:

- **`LeadRead` schema**: correct — `score_detalhes: dict | None`, no
  `exclude`/alias issue.
- **`GET /api/leads/{lead_id}`**: correct — verified with two
  independent round-trips, not just reading the code: an in-process
  `TestClient` call, and a *real* `uvicorn` process (temp SQLite DB,
  real HTTP over `127.0.0.1`, actual JSON login → bearer token → GET)
  bypassing `TestClient` entirely. Both returned
  `{"score_detalhes": {"breakdown": [...]}}` exactly matching what
  `compute_lead_score` persists.
- **`LeadDossierPage.tsx`**: the field name and condition
  (`lead.score_detalhes?.breakdown?.length`) were also already correct
  for the canonical shape.

No reproducible bug was found in the canonical pipeline path. The
likely actual trigger: `fetchLead`/`fetchLeads` in `api.ts` return
`res.json()` cast straight to `Promise<Lead>` — `fetch().json()` is
typed `any` by the DOM lib, so TypeScript provides **zero runtime
guarantee** the payload matches `Lead`/`ScoreDetalhes`. Any
`score_detalhes` that didn't arrive as the exact `{"breakdown": [...]}`
object (hand-seeded test data via raw SQL during manual testing, a
future backend change, etc.) would silently and permanently read as
"no score," with no error anywhere to point at the real cause — the
optional-chaining check degrades a shape mismatch into indistinguishable
"empty" instead of surfacing it.

**Fix**: extracted the breakdown-reading logic into a standalone, pure
function, `getScoreBreakdown(scoreDetalhes: unknown)` in
`frontend/src/leadScore.ts`. It treats the input as genuinely unknown
(matching how it actually arrives over the wire) and accepts both the
canonical `{ breakdown: [...] }` shape and a bare array as a fallback,
returning `[]` — never throwing — for anything else.
`LeadDossierPage.tsx`'s score section (`SecaoBreakdown`) now calls this
instead of reading `.breakdown` inline.

**Test added**: `frontend/src/leadScore.test.ts` (4 cases, using
`vitest` — see "Frontend (React 19 + Vite + TypeScript)" above for why
this dependency was added this session) — the first case is the direct
regression guard ("lead with score_detalhes filled returns the
breakdown, not empty"), confirmed to actually fail against a
deliberately broken always-`[]` implementation before being restored,
so it's a real guard, not a tautology. Also strengthened
`test_get_lead_com_score_calculado_retorna_breakdown_nao_vazio` in
`backend/tests/test_leads_routes.py` — unlike the pre-existing dossier
test (which hand-crafted a single breakdown item), this one runs the
**real** `montar_score_lead` engine to produce all 9 criteria, persists
them in the exact shape `_persistir_enriquecimento_lead` uses, and
asserts the API round-trips a non-empty 9-item breakdown with exactly
the fields `ScoreBreakdownItem` (frontend) expects.

### Lead dossier enrichment — fields that existed but weren't shown (2026-08-20)

Backend and frontend both touched in the same session, expanding the
dossier with data that was already computed/persisted somewhere but
never reached `LeadDossierPage.tsx` (or, in `mensagem_abordagem`'s
case, never existed as a field at all).

**Model (`app/models/lead.py`)** — three new columns:
- `mensagem_abordagem` (`Text`, nullable) — was a **display-only
  placeholder** in this session (no generation logic, no endpoint). Both
  were added in a follow-up session the same day — see "Suggested
  approach message generation" below for the full implementation.
- `divida_ativa_valor_total` (`Float`, nullable) and
  `divida_ativa_ajuizado` (`Boolean`, nullable) — closing a real gap:
  `app/services/pgfn.py::PgfnDebtSummary` has computed both
  (`valor_total`, `algum_ajuizado`) since the PGFN parser session, and
  `save_index_to_redis`/`lookup_debt_from_redis` already round-trip them
  through Redis — but `Lead` only ever persisted the count
  (`divida_ativa_qtd_inscricoes`). The two new columns don't change
  `pgfn.py` at all (it already produced the data); nothing yet writes
  them onto a `Lead` row (`_persistir_enriquecimento_lead` in
  `celery_app.py` still only sets `divida_ativa_qtd_inscricoes` — wiring
  the other two into that persistence step is a natural follow-up, not
  done this session since it wasn't asked for).

**Migration** `fbd6142f0632_adiciona_mensagem_abordagem_e_campos_...py`
chains after `a3f7c1d92b4e` (kanban_status/motivo_perda), all three
columns nullable with no `server_default` (unlike `kanban_status`,
`NULL` is already the correct "don't know yet" value here, no backfill
needed). **Validated against a real Postgres 16** — same
throwaway-cluster technique as the three previous migrations (port
5434 this time, socket dir a short `mktemp -d /tmp/pgsock_ma.XXXXXX`
per the 107-byte path lesson from the kanban migration): all four
migrations upgrade cleanly in chain order, `downgrade -1` reverts only
this one (kanban's columns and everything before stay intact),
re-upgrade is clean, and the resulting schema (`double precision`/
`boolean`/`text`, all nullable, no default) matches the model exactly.
Cluster stopped and deleted immediately after.

**Schema (`app/schemas/lead.py`)** — `LeadRead` gained the three new
fields plus `rqe_fonte`, `cnes_codigo`, and `updated_at`, which existed
on the model since earlier sessions but were never exposed by the
schema (an easy category of gap to miss: the field exists, nothing is
broken, it just silently never reaches the API). No `list_leads`
change needed — this is exactly the same `LeadRead`-is-shared-by-both-
routes situation already noted in the "Kanban" section above.

**Frontend (`LeadDossierPage.tsx`)**:
- **PGFN section**: now shows `divida_ativa_valor_total` formatted with
  `formatCurrencyBRL` (`src/format.ts`, `Intl.NumberFormat("pt-BR", {
  style: "currency", currency: "BRL" })`) inline in the warning sentence,
  and a separate, visually louder red badge
  ("⚠️ Execução judicial em andamento") when `divida_ativa_ajuizado` is
  true — deliberately a second, more alarming element, not just folded
  into the existing sentence, per the explicit request for something
  "visualmente destacado."
- **New "RQE / Especialidade" card** (`SecaoRqe`, added to the existing
  3-card grid as a 4th card, not a separate full-width section — fits
  the same `repeat(auto-fit, minmax(260px, 1fr))` grid without layout
  changes): three states by `(rqe_confirmado, rqe_fonte)` — confirmed +
  `fonte === "cnes"` → green "RQE confirmado via CNES" (high
  confidence); confirmed without CNES → gold "RQE mencionado no site
  (não confirmado via CNES)" (inference only); neither → neutral gray
  "RQE não confirmado." Colors deliberately reuse the same
  green/gold/gray vocabulary as `SignalLayer` elsewhere in this page
  (estruturado/inferência), not a new palette.
- **Kanban status badge** (`KanbanStatusBadge`, in the header, under the
  CNPJ line): looks up the display label from `kanbanStatuses.ts`'s
  `KANBAN_COLUMNS` (same source of truth the Kanban board itself uses —
  no duplicate label list). When `kanban_status === "perdido"`, also
  renders `motivo_perda` right next to the pill, not hidden behind a
  click — the dossier is meant to answer "why is this lead here" at a
  glance.
- **WhatsApp → `wa.me` link** (`CampoWhatsapp`): number formatting
  (`formatarNumeroWhatsapp` in `src/format.ts`) is a **deliberate
  mirror** of `app/services/whatsapp.py::formatar_numero_whatsapp` — same
  length-based (not prefix-based) heuristic for the country code, same
  reasoning (DDD 55 is Rio Grande do Sul, so a bare 11-digit local
  number can start with "55" without having the country code — see that
  module's docstring). Re-implementing this in TypeScript without also
  re-implementing the tests would be exactly the kind of silent
  transcription risk the previous bug-fix session's write-up warned
  about, so `src/format.test.ts` mirrors
  `backend/tests/test_whatsapp.py`'s cases one-for-one, including the
  DDD-55 trap case. The link only renders when the number formats to a
  plausible 12-13 digits — same "don't show a broken link" logic as the
  backend's "don't call the API with a clearly wrong number."
- **E-mail copy button** (`CampoEmail`): `navigator.clipboard.writeText`,
  local `copiado` state flips the button label to "Copiado!" for 1.5s
  (`setTimeout`). Clipboard failure (e.g. non-secure context) is
  swallowed silently — not critical enough to surface as a page error,
  matches this page's existing tolerance for degraded-but-non-fatal
  states.
- **`created_at`/`updated_at`** (`src/format.ts`): `formatDate` (`Intl.DateTimeFormat("pt-BR")`,
  `DD/MM/AAAA`) for `created_at` ("Encontrado em..."), `formatRelative`
  for `updated_at` ("Atualizado há X minutos/horas/dias" — falls back to
  `formatDate` past 30 days, since "há 4 meses" is too vague to inform a
  contact-priority decision).
- **Markup fix while wiring `CampoWhatsapp`**: extracted `Indicador`
  (just the `<span>` badge) out of `CampoIndicador` (which wraps it in
  its own `dt`/`dd`) — `CampoWhatsapp` needed the badge *inside* its own
  `dd` alongside the `wa.me` link, and nesting a full `CampoIndicador`
  (with its own `dt`/`dd`) inside another field's `dd` would have been
  invalid nested `dt`/`dd` markup. `CampoIndicador` itself now just
  wraps `Indicador` in the same `dt`/`dd` shape as before — no visible
  change for its existing callers.
- Same standing sandbox limitation as every other frontend screen so
  far — not tested in an actual browser; validated via `tsc -b`,
  `npm run build`, `npm run lint`, and `npm run test` (12 vitest cases:
  4 pre-existing in `leadScore.test.ts`, 8 new in `format.test.ts`).

### Suggested approach message generation (`gerar_mensagem_abordagem`, follow-up session, 2026-08-20)

Completes what the previous session deliberately left as a display-only
placeholder (`mensagem_abordagem` — see "Lead dossier enrichment"
above).

**`app/services/ai_enrichment.py::gerar_mensagem_abordagem(lead: dict,
client=None) -> str`** — same model (`claude-haiku-4-5-20251001`), same
raw-HTTP-via-injectable-`httpx.Client` pattern as `inferir_dados_site`
in this same module, but **deliberately more defensive**:
`inferir_dados_site` lets `resp.raise_for_status()` propagate on an
HTTP error (`test_erro_http_propaga_excecao` asserts this) — this
function catches `httpx.HTTPError` (covers both connection failures and
HTTP error statuses) and returns `""`, same as its handling of an empty/
missing text block in the response. The reason for the divergence:
`inferir_dados_site` runs inside `process_lead_pipeline`, where
`_rodar_etapa()` already catches any exception per stage — letting it
raise there is fine. `gerar_mensagem_abordagem` runs synchronously
inside an HTTP request handler with no such wrapper, and the caller
(the route) needs a single, uniform "did this work or not" signal
(empty string) to decide whether to persist or return an error.
Housekeeping while adding this: `_get_client` was moved above
`PROMPT_ABORDAGEM_TEMPLATE`/`gerar_mensagem_abordagem` in the file (it
used to sit right before `inferir_dados_site`, further down) so both
functions can call it without relying on Python resolving a
still-forward reference at call time — purely cosmetic, no behavior
change.

Prompt (`PROMPT_ABORDAGEM_TEMPLATE`) asks for a short (2-3 sentence)
first-contact email, explicit non-negotiable tone rule for PGFN debt
("oportunidade de revisão financeira", never "sua empresa está
endividada" — the exact phrasing the user specified), and an explicit
"don't invent data not in the list" instruction.
`_montar_contexto_lead(lead: dict)` only adds a bullet line for signals
that are actually present (razão social always included, everything
else conditional) — same "don't give the model room to hallucinate a
gap" principle as `inferir_dados_site`'s prompt. Takes a plain `dict`
(not the ORM `Lead`), same pattern as `montar_score_lead` in
`app/scoring/compute_lead_score.py` accepting pre-extracted data instead
of a database object.

**`POST /api/leads/{lead_id}/gerar-abordagem`** (`app/api/routes/leads.py`,
`get_current_user`, same protection as the rest of the leads routes):
404 if the lead doesn't exist; builds the `dict` from the `Lead` row's
relevant fields, calls `gerar_mensagem_abordagem`; if it returns `""`,
**502** ("Não foi possível gerar a mensagem agora. Tente novamente.")
**without touching `Lead.mensagem_abordagem`** — a failed regeneration
never wipes out a previously-generated message. On success, persists
and returns the full updated lead (`response_model=LeadRead`, same
pattern as `PATCH /status`).

**Tests**: `tests/test_ai_enrichment.py` gained 5 cases (success with
prompt-content assertions including the literal `"tom consultivo"`
instruction, empty response, HTTP error, network error — the last two
both assert `== ""`, not an exception). `tests/test_leads_routes.py`
gained 5 route-level cases, monkeypatching
`app.api.routes.leads.gerar_mensagem_abordagem` (not the service
module — same "monkeypatch at the call-site" convention documented in
"Pipeline orchestration" above) — success, persistence confirmed via
`db_session.refresh(lead)`, 404, empty-AI-response → 502 without
overwriting an existing message, and no-token → 401.

**Frontend (`LeadDossierPage.tsx`, only `SecaoAbordagem` touched)**:
`gerarAbordagem(token, leadId)` added to `api.ts` (POST, same
401/error-parsing pattern as the rest of that file). `SecaoAbordagem`
now owns its own `gerando`/`erro` state and calls `useAuth()` directly
(same self-contained pattern as `CampoEmail`'s copy-button state) —
`onGerada: (lead: Lead) => void` prop lets it hand the *entire* updated
`Lead` back up to `LeadDossierPage`'s `setLead`, keeping `lead` as the
single source of truth for the page rather than `SecaoAbordagem`
tracking its own divergent copy of the message. Empty state gets a
"Gerar mensagem com IA" button; populated state gets a smaller "Gerar
novamente" button below the text; both show "Gerando..." and disable
while in flight; a request error (network failure, or the 502 from an
empty AI response) renders a plain-language message under the button
and never removes the previously-shown text/button — the section always
has a next action, never a dead end.

**Validation note — this one's different from every other AI/HTTP
integration in this project**: `inferir_dados_site`, Google Places, and
Firecrawl are all marked "not tested against the real API" because
*this development sandbox* blocks authenticated outbound network calls
(see "Network access from Bash is blocked in this sandbox" above) —
that limitation is about where the code was *written*, not where it
*runs*. Clicking "Gerar mensagem com IA" in the actual dossier screen
**is** the live validation, performed by someone who already has
legitimate access to the production `ANTHROPIC_API_KEY` — this
principle still holds after the follow-up session below, it's just a
different endpoint now doing the calling. Don't add a "pending
validation" caveat for approach-message generation in README/CLAUDE.md
the way the other integrations have one; if the user reports a button
doesn't work, that's a real bug report against a live call, not a
"still needs testing" gap.

**⚠️ Superseded by the follow-up session below (same day)**:
`POST /api/leads/{lead_id}/gerar-abordagem` (no `{canal}` segment) and
writing to `Lead.mensagem_abordagem` — both described above — **no
longer exist in the code**. Replaced by a per-channel endpoint and a
`LeadMessage` history table; see "Multi-channel suggested approach"
immediately below. `Lead.mensagem_abordagem` itself is still a column
(deprecated, old data preserved), but the route that wrote to it was
deleted outright, not kept as a compatibility shim — its underlying
`gerar_mensagem_abordagem(dict)` signature doesn't exist anymore either
(now requires `canal`), so keeping the old route would have meant
either breaking it or forking the prompt logic. Everything above this
box is accurate as a record of what that session built; just don't
treat the endpoint path as currently live.

### Multi-channel suggested approach (`LeadMessage`, email/WhatsApp, follow-up session, 2026-08-20)

Replaces the single generic `mensagem_abordagem` (previous session,
above) with a proper per-channel history: each generation creates a new
row, never overwrites, and email/WhatsApp get distinct tone, length,
and format instead of one generic message reused for both.

**Model** (`app/models/lead_message.py`, new file): `LeadMessage` —
`id` (uuid), `lead_id` (FK → `leads.id`, indexed — queries always
filter by it), `canal` (`String(10)`, `CanalMensagem.value` —
`"email"` | `"whatsapp"`, same plain-string-plus-Python-Enum convention
as `Lead.kanban_status`/`User.role`), `conteudo` (`Text`, `NOT NULL` —
the route only ever creates a row once the AI produced something
non-empty), `assunto` (`Text`, nullable — only the `"email"` channel
fills it), `gerado_em` (`DateTime`, `default=datetime.utcnow`, no
`server_default` — same convention as `leads.created_at`/`updated_at`,
not the `server_default="novo_lead"` pattern `kanban_status` needed for
backfill). `CanalMensagem(str, Enum)` lives in this same file, not
`lead.py` — it's specific to messages, not the lead itself.
**Instagram deliberately excluded** from the enum — explicit
instruction this session, don't add it without a follow-up request.

**Migration** `c47a9e2b1d05_cria_tabela_lead_messages.py` chains after
`fbd6142f0632` (mensagem_abordagem/PGFN valor/ajuizado — the head at
the time). A new **table**, not new columns on `leads` — deliberately,
since this is a one-to-many relationship (a lead can have many
generated messages over time) that a single-row-per-lead column
couldn't represent. Registered in `alembic/env.py`'s model-import list
(`from app.models import lead, lead_message, user`) alongside the other
two. **Validated against a real Postgres 16** — same throwaway-cluster
technique as all four previous migrations (port 5435 this time, short
socket dir per the now-familiar 107-byte-path lesson): all five
migrations upgrade cleanly in chain order, `downgrade -1` reverts only
this one (the FK and everything before it stay intact — verified `leads`
still has its `mensagem_abordagem`/PGFN columns untouched after the
downgrade), re-upgrade is clean, and the resulting schema (FK to
`leads.id`, index on `lead_id`, `conteudo` `NOT NULL`, `assunto`
nullable) matches the model exactly. Cluster stopped and deleted
immediately after.

**`app/services/ai_enrichment.py::gerar_mensagem_abordagem`** signature
changed from `(lead: dict, client=None) -> str` to `(lead: dict, canal:
str, client=None) -> MensagemGerada` (new dataclass: `conteudo: str`,
`assunto: str | None = None`) — a **breaking change**, deliberate, not
maintained alongside a backward-compatible overload. `canal` outside
`CANAIS_VALIDOS` (`("email", "whatsapp")`) raises `ValueError` — this
is treated as a programming error (the route validates against
`CanalMensagem` before ever calling this function), not a case to
handle gracefully like a bad AI response.

Two prompt templates, built from a shared `_REGRAS_DE_TOM` string (the
PGFN-consultive-tone instruction — same wording as before, now
literally shared text instead of duplicated) via `.format()` at
*module load time*, not per-call:
- `PROMPT_ABORDAGEM_EMAIL_TEMPLATE` — 3-4 sentences, formal, asks for
  JSON (`{"assunto": ..., "corpo": ...}`), parsed with `_extrair_json`
  (the same tolerant-to-markdown-wrapping helper `inferir_dados_site`
  uses — moved earlier in the file, right after `_get_client`, so both
  functions can reach it without relying on Python's late name
  resolution to paper over a forward reference; purely cosmetic, no
  behavior change).
- `PROMPT_ABORDAGEM_WHATSAPP_TEMPLATE` — 1-2 sentences, casual, plain
  text response (no JSON) — same shape `gerar_mensagem_abordagem`
  already used for the single-channel version.

**Double-`.format()` gotcha worth knowing if you touch these
templates**: both are built as `"""...""".format(regras_de_tom=...)` at
import time, but still need `{dados_lead}` to survive that call
un-substituted for the *second*, per-request `.format(dados_lead=...)`
in `gerar_mensagem_abordagem`. That's why the source has `{{dados_lead}}`
(doubled braces) and the JSON block in the email template has
`{{{{`/`}}}}` (quadrupled) — each doubling survives exactly one
`.format()` pass. Verified empirically this session
(`PROMPT_ABORDAGEM_EMAIL_TEMPLATE.format(dados_lead=...)` printed and
eyeballed) after getting the brace count right on the first try but not
trusting it blindly — get this wrong and it's a `KeyError` at import
time (module fails to load at all), not a subtle runtime bug, so it's
at least a loud failure if a future edit miscounts the braces.

Both endpoints below build the `dict` passed to `gerar_mensagem_abordagem`
via a new shared helper, `_montar_dados_lead(lead: Lead) -> dict`, in
`app/api/routes/leads.py` — the exact same field list the old single
endpoint used to build inline; only route to call it is `gerar_abordagem`
now, but keeping it as a named function avoids the list getting
silently forked if a third endpoint needs it later.

**`POST /api/leads/{lead_id}/gerar-abordagem/{canal}`** (`get_current_user`,
same protection as the rest of the leads routes) — 422 for a `canal`
outside `CanalMensagem`'s values, 404 if the lead doesn't exist, 502
(without creating any row) if `gerar_mensagem_abordagem` returns an
empty `conteudo`. On success, **always creates a new `LeadMessage`** —
this route never does an `UPDATE`, that's the whole point of the
history model. `response_model=LeadMessageRead`
(`app/schemas/lead_message.py`, new file, mirrors the
one-schema-file-per-model convention `app/schemas/lead.py` already
established).

**`GET /api/leads/{lead_id}/mensagens`** (same protection) — 404 if the
lead doesn't exist, otherwise the *most recent* `LeadMessage` per
channel (never the full history — that stays queryable directly against
the table if a future session needs it, just not through this route).
Implemented by fetching all of a lead's messages ordered by
`gerado_em.desc()` and taking the first one seen per `canal` in Python
(`dict.setdefault`), not a `DISTINCT ON`/window-function query —
deliberately simple, since Postgres-only `DISTINCT ON` would also break
the SQLite test suite, and the realistic row count per lead (at most a
few dozen across two channels) makes the Python-side approach's
O(n) scan a non-issue.

**Frontend (`LeadDossierPage.tsx`, only `SecaoAbordagem` and its
children touched)**:
- `SecaoAbordagem` now fetches `GET /mensagens` in parallel with `GET
  /leads/{id}` (`Promise.all` in the page's main `useEffect`, both
  results land together) instead of relying on `lead.mensagem_abordagem`
  — the page's `mensagens: LeadMessage[]` state holds at most one entry
  per channel (`registrarNovaMensagem` replaces-not-appends by `canal`
  on every successful generation), matching what the backend's `GET
  /mensagens` already guarantees, so the frontend never has to
  reconcile a real history client-side.
- Channel visibility: the "E-mail" card renders only if `lead.email` is
  truthy; "WhatsApp" only if `lead.whatsapp_ativo === true` (**not**
  just having a phone number — same reasoning as `CampoWhatsapp`
  elsewhere on this page: generating a message for a number nobody's
  confirmed has WhatsApp doesn't make sense). If neither channel is
  available, the section renders a single explanatory line instead of
  two empty cards. Instagram never appears here, matching the backend's
  `CanalMensagem` enum not having a third value.
- Each channel is its own `CanalAbordagemCard`, self-contained
  (`gerando`/`erro` local state, `useAuth()` directly) — same
  self-contained-subcomponent pattern as `CampoEmail`'s copy button.
  Empty state: "Gerar mensagem com IA". Populated state: the
  message (plus `assunto` line, email only) with "Gerar novamente"
  below it. A request error renders under the button without clearing
  whatever was already showing — same "always leave a next action"
  principle as the single-channel version had.
- **Channel-specific action buttons**, both `encodeURIComponent`-encoded
  (handles accented characters and line breaks correctly — a raw
  template-literal URL would break on either):
  - Email: `mailto:{email}?subject={assunto}&body={conteudo}` — an
    ordinary link (`<a href="mailto:...">`), no `target="_blank"` since
    `mailto:` links don't open a browser tab, they hand off to whatever
    mail client is registered.
  - WhatsApp: `https://wa.me/{numero}?text={conteudo}` (`target="_blank"`,
    `numero` from the same `formatarNumeroWhatsapp` helper — see "Lead
    dossier enrichment" above — used by the unrelated "Abrir WhatsApp"
    quick-action link in the Contato card). **These are two different
    buttons with two different purposes**: the Contato card's "Abrir
    WhatsApp" opens an empty conversation regardless of any generated
    message; this section's "Enviar mensagem gerada" pre-fills the
    `text` param with the specific `LeadMessage.conteudo` just
    generated. Don't merge them — they answer different questions ("do
    I have a way to reach this lead on WhatsApp at all" vs. "send this
    specific AI-drafted message").
- `api.ts`: old single-canal `gerarAbordagem(token, leadId): Promise<Lead>`
  removed outright (the backend route it called no longer exists) and
  replaced with `fetchMensagens(token, leadId): Promise<LeadMessage[]>`
  and `gerarAbordagemCanal(token, leadId, canal): Promise<LeadMessage>`.
  `Lead.mensagem_abordagem` stays in the TS `Lead` interface (the
  backend schema still returns it, deprecated-but-present) but nothing
  reads it anymore.
- Same standing sandbox limitation as every other frontend screen —
  not tested in an actual browser; validated via `tsc -b`, `npm run
  build`, `npm run lint`, `npm run test` (unchanged: this session didn't
  add new frontend unit tests, since the changed logic — channel
  visibility, mailto/wa.me URL construction — is straightforward JSX/
  string-templating without the kind of subtle heuristic
  (`getScoreBreakdown`, `formatarNumeroWhatsapp`) that earned
  `leadScore.test.ts`/`format.test.ts` their own pure-function tests in
  prior sessions; reconsider if a bug report ever traces back to this
  section).

### Visual polish pass — "premium SaaS" (Sidebar, design tokens, icons, follow-up session, 2026-08-20)

Frontend-only session, all three existing screens (Login, Kanban, Lead
dossier) touched, no backend file changed. Structural addition (a
sidebar) plus a shared design-token layer plus per-screen depth/
typography/icon polish — not a rewrite of any screen's logic.

**⚠️ Dark theme is unchanged and deliberate — read this before touching
palette code.** `frontend/src/tokens.css` carries the authoritative
warning (top of file): the navy/gold dark theme (`--navy`, `--navy-dark`,
`--gold`, `--bg` in `index.css`) has been the client-validated identity
since the very first frontend session ("Frontend: Login + Kanban"
above). This session's "premium SaaS" pass adds *depth within that
theme* — shadows, a radial gradient background, sharper typography — it
does **not** introduce a light theme, a theme toggle, or any
`prefers-color-scheme` branching. If a future session is asked to "make
it look more premium" again, that does not mean revisit light vs. dark;
the dark theme is the answer, not an open question.

**`frontend/src/tokens.css`** (new, imported in `main.tsx` before
`index.css`): `--shadow-sm/md/lg` (dark-theme-appropriate — low-opacity
black, deliberately lighter than a typical light-theme shadow scale,
since a heavy shadow on an already-dark background just reads as a
black smear with no definition), `--shadow-gold-hover` (a
brand-colored shadow used on primary-button/interactive-card hover,
distinct from the neutral shadows), `--transition-fast` (150ms)/
`--transition-smooth` (250ms), a `--space-1`...`--space-12` scale
(4px multiples), `--bg-gradient` (`radial-gradient(ellipse at top
left, #1a1642 0%, #0b0d22 55%)` — applied to `body` in `index.css`,
replacing the old flat `background: var(--bg)`; the gradient's lighter
stop is still a dark navy, not a new hue), and `--sidebar-width`
(240px — the single source of truth `Sidebar.css` and
`AuthenticatedLayout.css` both read, though in practice only
`Sidebar.css` uses it directly since the layout is flex-based, not
margin-based — see below). Also defines the `.spin`/`@keyframes spin`
utility class used by the AI-generation loading spinner (see "Suggested
approach message generation" above) — centralized here because it's
generic enough for any future spinner, not tied to one screen.

**Sidebar** (`components/Sidebar.tsx` + `Sidebar.css`, new) — present on
Kanban and the lead dossier, absent on Login (composed in per route, see
below, not baked into every route unconditionally). Contents, top to
bottom: brand block ("HunterPro" / "Minotto Contabilidade"), nav
(`Link` to `/` labeled "Kanban", highlighted via `useLocation().pathname
=== "/"`; two `aria-disabled` non-navigating `<span>` items — "Busca de
Leads", "Dashboard" — each with an "em breve" pill; **no route exists
for either**, and none should be added just because the sidebar has a
slot for it — the slot is reserved space, not a promise pending
implementation this session), footer (user role + `LogOut` button,
moved here from the old Kanban page header).

**User role, not user name, in the sidebar footer** — `Sidebar.tsx`
has a local `decodeRole(token)` that base64-decodes the JWT payload
client-side (no signature verification — there's nothing to verify
against client-side anyway, and this is read-only display of a claim
already trusted by virtue of a successful login, not a security check).
The payload only carries `user_id` and `role` (`app/api/routes/auth.py`
— no email, no name), so the footer shows "Administrador"/"Cliente",
not a real name. Showing an actual name/email would need a backend
change (payload field or a `GET /api/auth/me`) — explicitly out of
scope for a visual-polish-only session; documented as a follow-up in
README "O que falta" rather than silently faked with placeholder text.

**`AuthenticatedLayout.tsx` + `.css`** (new) — a thin composition
wrapper (`<div class="app-layout"><Sidebar/><main
class="app-content">{children}</main></div>`), deliberately kept
separate from `ProtectedRoute` (auth guard only). `App.tsx` composes
them explicitly per protected route:
`<ProtectedRoute><AuthenticatedLayout><KanbanPage
/></AuthenticatedLayout></ProtectedRoute>` — two single-purpose
wrappers, not one component doing both jobs. "Fixed" sidebar is
implemented as `position: sticky; top: 0; height: 100vh` on `.sidebar`
inside a flex row (`.app-layout { display: flex }`), **not** `position:
fixed` with a manually-matched `margin-left` on the content — sticky-
inside-flex lets the flex layout itself reserve the sidebar's width, so
there's no second place that has to agree with `--sidebar-width` if it
ever changes.

**`lucide-react`** (new dependency, the only one added this session)
replaces the three emoji/text-arrow glyphs that existed:
`⚠️` (PGFN ajuizado warning) → `AlertTriangle`; `←` (dossier back
button) → `ArrowLeft`; `↗` (WhatsApp quick-action link) → `ExternalLink`.
Every dossier section `<h2>` gained an icon via a new shared component,
`SectionHeading` (in `LeadDossierPage.tsx`, takes the icon *component*
via `LucideIcon` type, not a pre-instantiated element, so size is
controlled in one place): `Building2` (dados cadastrais), `Phone`
(contato), `Globe` (presença digital), `Stethoscope` (RQE), `Sparkles`
(abordagem sugerida — signals "AI-generated"), `Gauge` (breakdown do
score). The PGFN section is the one exception with a static
`SectionHeading` usage — its `<h2>` picks between `Landmark` (has
active debt) and `ShieldCheck` (no pendency) inline, since the icon
itself carries state, not just decoration.

**Badges → pills, more contrast, across all three badge families**
(explicit ask covered all three, not just priority):
- `PriorityBadge` (`index.css`, shared) — already a pill before this
  session; contrast bumped (`rgba(...,0.22)` fill, lighter foreground
  hex per color) and a `::before` colored dot added. Gained a `size`
  prop (`"sm" | "lg"`, default `"sm"`) — `"lg"` (`.priority-badge-lg`)
  is used only in the dossier header, where the badge needs more
  presence next to the enlarged score number (see below); the Kanban
  card still uses the default small size.
- Kanban status pill (`.dossier-kanban-pill`) — `border-radius` went
  from a rounded-rect (6px) to a true pill (999px), background opacity
  raised.
- Score-breakdown layer tags (`.dossier-breakdown-layer-tag`) — this
  one was previously **plain gray text**, not a badge at all, despite
  being one of the three families the request named. Now a real pill,
  colored to match its layer (green/gold/light-blue, same hues as the
  item's left border) via `.layer-estruturado .dossier-breakdown-layer-tag`
  etc. — the layer color is now readable at a glance in the badge
  itself, not only inferred from a 3px border sliver.

**Buttons** — every interactive button/link across all three screens
picked up `transition` (using the new tokens), a `:hover` state
(elevation via `transform: translateY(-1px)`/`-2px` plus
`--shadow-gold-hover` on gold-background buttons, border/color shift on
outlined ones), and an explicit `:disabled` state (`opacity: 0.7`,
`transform: none` — so a disabled button doesn't also look "lifted").

**Login** (`LoginPage.tsx`/`.css`) — gradient is inherited from `body`
(no page-specific background rule needed, single source of truth).
Card padding increased, `max-width` 360px → 400px, `box-shadow:
var(--shadow-lg)` added, a `Building2` icon and a one-line subtitle
inserted above the fields for stronger visual hierarchy. Submit button
gained the same hover-elevation treatment as other primary buttons.

**Kanban** (`KanbanPage.tsx`/`.css`, `KanbanColumn.tsx`) — old page
header (eyebrow "HunterPro" / title "Minotto Contabilidade" / logout
button) removed entirely — brand and logout both live in the sidebar
now; replaced with a smaller, page-specific header ("Pipeline
comercial" / "Kanban de Leads"). `KanbanColumn` renders "Nenhum lead
aqui ainda" (`.kanban-column-vazia`, dashed border, muted text) when
its `leads` array is empty, instead of a blank body — the `useDroppable`
ref stays on the parent `.kanban-column-body` regardless of whether the
placeholder or real cards render inside, so drop targets are unaffected.
Cards gained `box-shadow` + hover elevation (`translateY(-2px)`,
`--shadow-md`, brighter border) to read as interactive. **Column-move
"transition" is a card mount-in animation, not a position transition**
— `@keyframes kanban-card-entrar` (fade + slight translateY, on every
`.kanban-card` mount) is the pragmatic version of "smooth transition
between columns": moving a lead to a different column changes the
card's DOM parent, so React unmounts/remounts it there — there's no
single element whose position CSS can animate across that boundary
without a FLIP-style library (`framer-motion` or similar), which
wasn't added this session (documented as a deferred follow-up in
README, not silently skipped).

**Lead dossier** (`LeadDossierPage.tsx`/`.css`) — the most-touched
screen, per the request calling it "a mais importante — é a 'vitrine'
do produto":
- Every section header now uses `SectionHeading` (see lucide-react
  above).
- `dt`/`dd` typographic hierarchy: `dt` (label) shrunk to 11px,
  uppercase, letter-spaced, gray; `dd` (value) bumped to 14px,
  `font-weight: 600`, white — previously both were the same 13px with
  no weight distinction.
- A subtle divider (`border-bottom: 1px solid rgba(255,255,255,0.05)`)
  separates each `.dossier-campo` row from the next inside a card's
  `<dl>` (removed on `:last-child` — no double border against the
  card's own edge). This is the concrete interpretation of "divisores
  sutis entre seções" — at the field-row level inside each card, not as
  a separate divider element between the major page-level blocks
  (header/PGFN/grid/abordagem/breakdown), which already have their own
  card backgrounds/borders separating them structurally.
- Score total: 40px → 60px, `text-shadow: 0 0 24px rgba(245,200,66,0.25)`
  added — deliberately the single largest number on the page, per the
  explicit "mais presença visual, com destaque real" request.
  `PriorityBadge` in the header uses `size="lg"`.
- AI-generation loading state: both "Gerar mensagem com IA" and "Gerar
  novamente" buttons swap their label for a bare `Loader2` icon
  (`.spin` from `tokens.css`) while `gerando` is true — **icon only,
  no text alongside it** (the request said replace the text, not
  augment it), with `aria-label`/`aria-busy` covering accessibility
  since there's no longer a visible text cue. Buttons got a `min-width`
  so swapping text↔icon doesn't visibly resize them.
- `mailto:`/`wa.me` action links (added in the previous "multi-channel
  suggested approach" session) gained `Mail`/`Send` icons; no logic
  change to the URL construction itself.

**Not visually validated — read this before assuming the result looks
right.** This sandbox has no browser (same standing limitation as every
other frontend session — see "Network access from Bash is blocked in
this sandbox" above), and this is the first session where that
limitation applies to *layout/CSS*, not just API calls. Every
color/spacing/shadow value here was chosen deliberately and is
documented, but none of it has been visually confirmed on an actual
rendered page. `npm run build`/`npm run lint`/`npm run test` all pass —
that confirms TypeScript types, lint rules, and the 12 pre-existing
`vitest` cases (none of which cover rendering/CSS) are intact, **not**
that the visual result matches intent. Treat this whole session's
output as "implemented and self-consistent, pending a real look."

### Migrations (`backend/alembic/`)

**`alembic/versions/` was empty before 2026-08-20** — there was no
baseline migration for the `leads` table (`app/models/lead.py`), even
though that model and its API route have existed since the project's
first commit. First fixed incompletely (only `6102ef9fc509_cria_tabela_users.py`
got written, with `down_revision=None` as if it were the root) and then
corrected the same day once asked to double check: **two** chained
migrations now exist —
`e119018824e8_cria_tabela_leads.py` (`down_revision=None`, the actual
root — `leads` is the older model, so it migrates first) →
`6102ef9fc509_cria_tabela_users.py` (`down_revision="e119018824e8"`).
No data dependency between the two tables; the ordering is purely
"older model migrates first."

Both were written by hand, not via `alembic revision --autogenerate` —
the Postgres in this project's own `settings.DATABASE_URL` (from
`.env`) is still unreachable in this sandbox (auth failure even with
the real configured URL), so autogenerate had nothing to diff against
*for this project's actual database*. That said, **the migration files
themselves have been validated against a real Postgres 16**, just not
that specific one: a throwaway cluster was provisioned in-session
(`initdb` + `pg_ctl`, own data dir under `/tmp`, a non-default port,
Unix socket only — deliberately isolated from whatever unrelated
Postgres service was already listening on 5432 in this environment) —
`alembic upgrade head` applied both migrations cleanly, `alembic
downgrade base` reverted both cleanly, and the resulting schema was
diffed column-by-column (`\d leads`, `\d users`) against the SQLAlchemy
models — exact match on types, nullability, and both unique indexes.
The cluster was stopped and deleted immediately after. **Still
unvalidated**: these migrations against this project's actual configured
database — that would need either working `.env` credentials or a
Postgres reachable some other way.

`alembic/env.py` imports both `app.models.lead` and `app.models.user` so
a future `--autogenerate` run (once the real DB is reachable) picks up
both — and, per the above, would now correctly recognize the schema as
already up to date rather than trying to create `leads` a second time.

A **third** migration was added 2026-08-20 (same day, later session):
`a3f7c1d92b4e_adiciona_kanban_status_e_motivo_perda.py`
(`down_revision="6102ef9fc509"`, adds `Lead.kanban_status`/
`Lead.motivo_perda` — see "Kanban" above). Also hand-written for the
same `DATABASE_URL`-unreachable reason. **Now validated against a real
Postgres 16** too (same session, later request): another throwaway
cluster was provisioned (`initdb`/`pg_ctl`, own data dir under the
scratchpad, port 5433, Unix socket only) — one snag worth knowing for
next time: the socket directory can't live inside the deep
scratchpad path (`/tmp/claude-<uid>/-<project>/<session-uuid>/scratchpad/...`)
because Postgres's Unix-socket path has a hard 107-byte limit and that
path exceeds it (`could not create any Unix-domain sockets`) — the fix
was to put just the socket dir under a short `mktemp -d /tmp/pgsock_kb.XXXXXX`
while keeping the actual data dir in the scratchpad. Confirmed: `alembic
upgrade head` applies all three migrations in the correct chained order
(`leads` → `users` → this one), `alembic downgrade -1` reverts only this
one (back to the `users` revision, `leads`/`users` tables and their
indexes untouched), `alembic upgrade head` re-applies cleanly, and the
resulting `leads.kanban_status`/`leads.motivo_perda` columns
(`character varying(20) NOT NULL DEFAULT 'novo_lead'` /
nullable `text`) match `app/models/lead.py` exactly, both before and
after the downgrade/re-upgrade round-trip. Cluster stopped and its data
dir + socket dir fully deleted immediately after — same
not-the-project's-real-`DATABASE_URL` caveat as the other two
migrations still applies.

A **fourth** migration was added 2026-08-20 (same day, later session):
`fbd6142f0632_adiciona_mensagem_abordagem_e_campos_...py`
(`down_revision="a3f7c1d92b4e"`, adds `Lead.mensagem_abordagem`/
`Lead.divida_ativa_valor_total`/`Lead.divida_ativa_ajuizado` — see "Lead
dossier enrichment" above). Same throwaway-cluster validation as the
third migration (port 5434 this time, short socket dir per the 107-byte
lesson above): all four migrations upgrade in chain order, `downgrade
-1` reverts only this one, re-upgrade is clean, schema matches the
model exactly (all three columns nullable, no default — unlike
`kanban_status`, `NULL` is already the correct value here, no backfill
needed). Cluster stopped and deleted immediately after.

A **fifth** migration was added 2026-08-20 (same day, a further
follow-up session): `c47a9e2b1d05_cria_tabela_lead_messages.py`
(`down_revision="fbd6142f0632"`, creates the `lead_messages` table for
`LeadMessage` — see "Multi-channel suggested approach" above). Unlike
the previous three, this one creates a **new table** rather than adding
columns to `leads` (one-to-many: a lead can have many generated
messages). Same throwaway-cluster validation (port 5435, short socket
dir): all five migrations upgrade cleanly in chain order, `downgrade
-1` reverts only this one (confirmed `leads`' own columns from the
fourth migration are untouched after), re-upgrade is clean, schema
(FK to `leads.id`, index on `lead_id`, `conteudo` `NOT NULL`, `assunto`
nullable) matches the model exactly. Cluster stopped and deleted
immediately after.

### Deployment note (EasyPanel)

Backend, Celery worker, and frontend are deployed as **separate App
Services** — Docker Compose multi-service setup is not well supported on
EasyPanel (lesson carried over from the SR Contábil project). The frontend's
nginx must point at the backend's public domain, not an internal Docker
hostname.
