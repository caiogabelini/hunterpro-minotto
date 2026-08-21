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
  objects). **⚠️ Superseded (22/08/2026)**: this paragraph originally
  said `require_admin` "doesn't protect anything yet — no admin-only
  route has been built." That's no longer true — `app/api/routes/admin.py`
  (the "busca mensal" panel, see "Admin panel" below) is the first
  route to actually use it, exercised through HTTP now too
  (`tests/test_admin_routes.py`). `GET /api/leads` still requires only
  `get_current_user` (any authenticated role), not `require_admin` —
  that part is still accurate.
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

**⚠️ SUPERSEDED (21/08/2026) — the paragraph below is a historical
record, not current guidance.** This session's warning said the dark
theme was permanent and that "premium" ≠ revisiting light vs. dark. A
later session (21/08/2026) did exactly that, on explicit client-driven
instruction: a full retheme to a LIGHT theme anchored on Minotto's real
brand (navy-dominant, gold as a minor accent only). See "Complete
retheme to light mode" further below for the current palette and every
color/contrast decision — `--navy`/`--navy-dark`/`--gold`/`--bg` no
longer exist as variable names at all (renamed, not aliased). The
original (now-outdated) reasoning is kept below only so future sessions
can see *why* dark was chosen originally, not as standing instruction.

~~**Dark theme is unchanged and deliberate — read this before touching
palette code.**~~ `frontend/src/tokens.css` carried the authoritative
warning (top of file): the navy/gold dark theme (`--navy`, `--navy-dark`,
`--gold`, `--bg` in `index.css`) had been the client-validated identity
since the very first frontend session ("Frontend: Login + Kanban"
above). This session's "premium SaaS" pass added *depth within that
theme* — shadows, a radial gradient background, sharper typography — it
did **not** introduce a light theme, a theme toggle, or any
`prefers-color-scheme` branching. ~~If a future session is asked to
"make it look more premium" again, that does not mean revisit light vs.
dark; the dark theme is the answer, not an open question.~~

**`frontend/src/tokens.css`** (new, imported in `main.tsx` before
`index.css`) — ⚠️ **`--shadow-gold-hover` and `--bg-gradient`, named
here, no longer exist** (renamed/removed in the 21/08/2026 retheme, see
below): `--shadow-sm/md/lg` (dark-theme-appropriate — low-opacity
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

**Follow-up bug found by the user's real look (2026-08-20, same day)**:
exactly the kind of gap the caveat above predicted. The Login email/
password `<input>` fields rendered with the browser's native light
background instead of the dark theme — `.login-field input` had
`background: rgba(0,0,0,0.25)` (a translucent black over the dark card),
which is fine for the normal state, but browser-native form-control
chrome and autofill styling don't reliably respect a translucent
background, and Chrome's autofill specifically **ignores
`background`/`color` on `<input>` entirely** once a field is
autofilled, painting its own white/yellow background no matter what
CSS is set. Fixed:
- Added `--bg-elevated: #141737` to `index.css`'s `:root` — a **solid**
  hex color (not translucent), deliberately: the WebKit autofill
  override trick (below) paints via `box-shadow` inset, and an
  alpha-blended color there lets the browser's own autofill background
  show through at the edges. `#141737` is a solid approximation of what
  the old `rgba(0,0,0,0.25)` over `--navy-dark` actually rendered as
  (same visual weight, just baked into one opaque value so it works as
  an inset-shadow fill too). `.login-field input` now uses this instead
  of the translucent black.
- Added `color-scheme: dark` to `:root` (`index.css`) — nudges browser-
  native UI (scrollbars, unstyled form chrome, some autofill dropdown
  affordances) toward dark rendering by default, independent of the
  autofill-specific fix below.
- The actual autofill fix (`LoginPage.css`): `input:-webkit-autofill`
  (plus `:hover`/`:focus`/`:active` variants, since Chrome/Safari apply
  the autofill background per-state and a plain unstated rule doesn't
  cover all of them) gets `-webkit-box-shadow: 0 0 0 1000px
  var(--bg-elevated) inset` (a shadow spread wide enough to fill the
  entire input, functionally replacing the background WebKit refuses to
  let CSS override directly) plus `-webkit-text-fill-color: #fff` (WebKit
  also ignores plain `color` on autofilled text — this specific
  property is the one it respects) and a **very long `transition` on
  `background-color`/`box-shadow`** (`9999s`) — this is the standard
  trick to delay/suppress the brief white flash Chrome paints the
  instant autofill fires, before the CSS above "wins." A separate
  `input:autofill` rule (unprefixed — Firefox's pseudo-class, which
  unlike WebKit *does* respect plain `background-color`/`color`, so no
  box-shadow trick needed there, but the rule still has to exist or
  Firefox's own default autofill background applies instead).
- `.login-field input:-webkit-autofill:focus` additionally reasserts
  `border-color: var(--gold)` — without it, an autofilled-and-focused
  field would keep whatever border the autofill state left it in
  instead of showing the normal focus ring.
- Scoped to `LoginPage.css` only, matching the report — no other screen
  has a bare `<input>` styled this way (`LossReasonModal`'s field is a
  `<textarea>`, not subject to browser autofill styling the same way,
  and wasn't part of the report).

### Dossier tabbed restructure (`LeadDossierPage.tsx`, follow-up session, 2026-08-20)

Complete restructure of the dossier's information architecture, modeled
on a reference screenshot the client shared from another product
("Radar Comercial") — a fixed header + 4 horizontal pill tabs (Dados /
Contatos / Análise IA / Mensagens), replacing the earlier single
long-scrolling page. **Explicit constraint honored**: every value shown
comes from data the dossier already had — no new backend fields, no new
AI call. Also explicitly deferred per the client-aligned plan: no
"Insights da IA" side panel this session — don't add one without a
follow-up request naming it.

**Fixed header** (`DossierHeader`) — razão social, `nome_fantasia`/CNPJ
(kept even though the reference screenshot didn't show them explicitly;
"only use existing data" was about not inventing fields, not about
deleting already-useful identifying info), then three new derived
lines, all computed from fields the dossier already exposed:
- **Secondary line** (`município/UF · nicho`) — nicho is the fixed
  string `"Clínica Médica"` (`NICHO_FIXO`), not derived from `cnae_principal`,
  because there's no niche-friendly-name mapping anywhere in this
  codebase and the request explicitly offered this as the simpler
  acceptable option ("pode usar CNAE como texto, ou 'Clínica Médica'
  fixo") — every lead in this project is already filtered to this niche
  at the seed stage (`app/services/receita_federal_bulk.py`, CNAE group
  8630), so the fixed string is accurate for 100% of leads, not a
  simplification that could mislead.
- **Resumo line** (`Score: X · Classificação: ... · Prioridade: ... ·
  Canal: ...`) — `Classificação` is a new label mapping over the
  existing `prioridade` (A/B/C) field: `CLASSIFICACAO_LABELS = {A:
  "Excelente", B: "Bom", C: "Regular"}`, exactly the mapping the request
  specified. `Canal` is `canalPreferido(lead)`: WhatsApp if
  `whatsapp_ativo`, else E-mail if `email_validado`, else `"—"` —
  deliberately gated on the *confirmed* signal (not mere field
  presence), matching the same gating `CampoWhatsapp`/`SecaoAbordagem`
  already use elsewhere on this page; WhatsApp is checked first to
  match the existing "WhatsApp-first" ordering convention already
  visible in the Contato card and the quick-action links.
- **Footer row** — the existing Kanban status pill + `motivo_perda` +
  "Encontrado em/Atualizado" text, unchanged content, just moved inside
  the header card as its bottom row (was a separate paragraph below the
  header before).

**Where the giant score number went**: the previous session's "hero"
score display (60px, gold glow, large `PriorityBadge`) lived in a
right-hand column of the header. That column is gone — **judgment call,
not explicit in the request**: since the new resumo line already shows
`Score: X` as compact text, keeping the giant number *also* in the
header would be redundant with itself. Read the "Análise IA" tab
instruction literally instead — "mova pra cá o breakdown do score...
com visual mais forte no número total (como já está, com o brilho
dourado)" — as an instruction to relocate the entire hero-score
treatment (badge + number + label, same CSS classes,
`.dossier-analise-score` is just a new container) into that tab, not to
duplicate it. If this reads wrong once actually seen on screen, moving
it back to the header (or showing both) is a small, contained change —
flagged here specifically so a future session doesn't have to
re-derive the reasoning.

**Tabs** (`AbaNav`, `ABAS` config array) — local `useState<Aba>("dados")`
in `LeadDossierPage`, no URL/query-param sync (not asked, would add
complexity for a feature that isn't needed yet — reconsider if deep-
linking to a specific tab is ever requested). Switching tabs unmounts
the previous tab's subtree entirely (plain conditional rendering, not
`display: none`) — this is safe because all persistent state
(`lead`, `mensagens`) lives in the parent component, not in any
per-tab component; only transient UI state (`CanalAbordagemCard`'s
`gerando`/`erro`) resets on tab-away, which is the correct behavior
for "in-flight loading state," not data loss.

**Aba "Dados"** (`AbaDados`) — the exact same three cards (Dados
cadastrais / Contato / Presença digital) plus the RQE card, same
fields, same components (`Campo`, `CampoWhatsapp`, `CampoEmail`,
`SecaoRqe`) — purely moved inside a new wrapper, zero field changes.
PGFN section (`SecaoPgfn`) sits above the card grid, unchanged
component, just reordered to be first per the explicit "most critical
info" instruction (it already was first before this session too, so
this is actually a no-op relative to the prior layout — worth noting
so nobody goes looking for a diff that isn't there).

**Aba "Contatos"** (`AbaContatos`, new) — **deliberately duplicates
data already visible in the Dados tab's Contato card** — this is
correct per the request, not an oversight: Dados tab shows raw fields
(spec-sheet style, `dt`/`dd` rows); Contatos tab shows the same
underlying data as a *person-centric* card with clickable channel
chips. Structured as `<ul><li>` (`.dossier-contatos-lista`/
`-contato-card`) even though today only one contact
(`decisor_nome`) can ever exist on a `Lead` — explicit request: "estruture
como uma lista que comporta mais contatos no futuro." A future
multi-contact model would map onto this same markup by rendering more
`<li>`s, no restructure needed. Chip gating, each independently
justified:
- WhatsApp chip: `whatsapp_ativo === true` **and** a valid formatted
  number (`formatarNumeroWhatsapp` succeeds) — same "don't offer an
  action for an unconfirmed channel" rule as the rest of the page.
- E-mail chip: `lead.email` truthy — **not** gated on `email_validado`,
  unlike the header's `canalPreferido` — deliberate difference: the
  header's "Canal" line is about which channel to *lead with*
  (should be a confirmed-good one), the Contatos chip is just "here's a
  way to reach them" (a raw, unvalidated email is still a real contact
  attempt worth offering as a chip). If this distinction turns out to
  be confusing in practice, the fix is a one-line gate change, not a
  restructure.
- LinkedIn chip: `linkedin_decisor` specifically (the *person's*
  LinkedIn, matching "pra ele" in the request), not `linkedin_empresa`
  (the company's).
- **Icon substitution, not a missing feature**: the LinkedIn chip uses
  `Link2` (a generic link icon), not a LinkedIn logo — `lucide-react`
  1.33 (the version installed in the previous polish session) doesn't
  export brand/logo icons at all (removed upstream for trademark
  reasons; confirmed by grepping the package's own `.d.ts` for
  `Linkedin`/`*Link*` declarations — only `Link2`/`Link2Off`/`Unlink2`
  exist). The chip's own "LinkedIn" text label already disambiguates,
  so this isn't a visual regression, just a constraint of the icon
  library as currently pinned.

**Aba "Análise IA"** (`AbaAnalise`, replaces the old standalone
`SecaoBreakdown`) — hero score block (see above) + a new "Sinais
positivos" chip list + the unchanged 9-criterion breakdown list.
"Sinais positivos" is **derived reorganization of `score_detalhes`,
not a new signal or a new AI call** (explicit constraint in the
request, worth restating since it's easy to reach for "just call the
AI for this" reflexively): `breakdown.filter(item => item.weight > 0 &&
item.points >= item.weight - 0.01)`. Two things worth knowing about
that filter if you touch it:
- `weight > 0` excludes `rating_google` specifically — its weight was
  confirmed at 0 by the client ("pra mim não importa", see
  `app/scoring/rules.py`), and without this exclusion a lead with no
  Google rating data (`points = 0`, `weight = 0`) would technically
  satisfy `points >= weight` and get listed as a false "positive
  signal" for a criterion that structurally can never produce one.
- The `- 0.01` tolerance exists because `points`/`weight` cross the
  network as floats computed server-side (e.g. `pgfn_points`: `6 ×
  (20/6)` doesn't always land on exactly `20.0` in IEEE 754) — a strict
  `>=` without the epsilon would silently drop a criterion that's
  visually "clearly full" in the breakdown list right above it from the
  "positive signals" list, which would read as a bug to anyone
  comparing the two lists side by side.
- Every criterion's own `label` (already a client-approved string from
  `SCORING_CRITERIA`) is reused verbatim for the chip text — no new
  copy was written, including for `divida_ativa_pgfn`: a full-weight
  PGFN debt score genuinely is a positive *prospecting* signal for an
  accounting firm's business model (more debt registrations = more
  reason for Minotto to reach out), so it correctly earns a "Dívida
  ativa PGFN" chip here even though the PGFN section elsewhere on the
  page frames the same fact as something to "verify before advancing" —
  both framings are accurate for their respective audiences (sales
  signal vs. deal-risk note), not a contradiction to reconcile.

**Aba "Mensagens"** — `SecaoAbordagem`/`CanalAbordagemCard`, generation/
regeneration/mailto/wa.me logic completely unchanged, only relocated
into the tab-conditional render instead of always rendering below the
old score-breakdown card.

**Not visually validated** — same standing sandbox limitation as every
other frontend session (no browser here). This is the fourth
consecutive dossier-touching session with that caveat; if the client's
reference screenshot implies a layout detail this write-up
misjudged (tab styling, header line order, chip placement), that's the
first thing to check against the real screen, not a sign the
implementation is fundamentally wrong.

**Follow-up fixes from real visual feedback (same day, 2026-08-20)** —
exactly the kind of correction the caveat above anticipated; the tabbed
restructure's own judgment calls turned out to need a real look after
all:

1. **The standalone "RQE / Especialidade" card is gone.** It used to
   sit as the 4th card in the Dados tab's `.dossier-grid` (3 cards fit
   the grid's `repeat(auto-fit, minmax(260px, 1fr))` evenly, a 4th one
   left alone on its own row — visually orphaned, per the report). RQE
   is a strategic ICP-fit signal (confirms the lead is actually a
   medical practice), so it moved to the **fixed header's resumo line**
   instead of living inside one tab — same reasoning that already put
   PGFN debt at the top of the Dados tab and the hero score in the
   header itself: information that shapes whether to pursue the lead at
   all shouldn't be one click away.

   `RqeResumoBadge` (new, replaces the deleted `SecaoRqe`) renders
   **nothing** (`return null`) when `rqe_confirmado` is falsy — this is
   a deliberate behavior change from the old card, which always
   rendered a third state ("RQE não confirmado", `.rqe-desconhecido`).
   The explicit ask was to omit the badge entirely in that case, not
   show an empty/negative state permanently in the header. `.rqe-desconhecido`
   and the old base-sizing class `.dossier-rqe-badge` were removed from
   the CSS as dead code; `.rqe-alta-confianca`/`.rqe-inferencia` (the
   green/gold color classes) survive unchanged and are now the *only*
   thing `RqeResumoBadge` reaches for — same colors the client had
   already seen on the old card, just relocated. The badge carries its
   own leading `·` separator inside the same conditional (`return null`
   for the whole `<><span className="dossier-resumo-sep">·</span>...</>`
   fragment, not just the pill) specifically so an absent badge never
   leaves a dangling separator with nothing after it.

2. **The hero score number is back in the header's top-right corner**,
   exactly where it lived before the tabbed restructure:
   `PriorityBadge size="lg"` above, the 60px gold-glow `.dossier-score-total`
   number, `.dossier-score-label` ("pontos de score") below. `.dossier-header`
   is a two-column flex row again (`.dossier-header-principal` on the
   left holding everything that used to be the header's only column,
   `.dossier-header-score` on the right, unchanged CSS from before the
   restructure — just re-added). **This reverses a judgment call from
   the tabbed-restructure session** that guessed the hero number would
   be redundant next to the resumo line's "Score: X" text and moved it
   into the Análise IA tab instead — wrong guess, confirmed by the
   user: the number needs to be visible on *every* tab, not hidden
   behind a tab click. `AbaAnalise` no longer renders any score-hero
   block at all (the `.dossier-analise-score` CSS rule was deleted) —
   it now goes straight from the card's top into "Sinais positivos"/
   "Breakdown do score"; its own `scoreTotal`/`prioridade` props were
   dropped for the same reason (`noUnusedParameters` in
   `tsconfig.app.json` would otherwise fail the build on an unused prop
   — this project's TS config catches that class of dead code by
   construction, worth remembering next time a prop's only consumer
   gets deleted).

   The resumo line's own "Score: X" text item, by contrast, **was**
   removed — the user's judgment call, explicitly delegated ("use seu
   julgamento pra não ficar redundante"): once the giant glowing number
   sits two inches away in the same header card, a second plain-text
   restatement of the identical number serves no purpose the way
   "Classificação"/"Canal" (which have no other visual representation
   anywhere on the page) still do. If this reads as *too* sparse once
   seen for real, adding "Score: X" back to the resumo line is a
   one-line JSX change, not a structural one.

**Lesson for whoever touches this header next**: don't move the hero
score number out of `.dossier-header` again without a specific request
naming that change — it went `header → Análise IA tab → header` in the
span of two sessions the same day, purely from guessing at redundancy
rather than being told to move it. The resumo line is the part that's
actually still open to editorial judgment (add/remove text items as
requested); the corner score block is not.

### AI-generated strategic insights (`gerar_insights_estrategicos`, "Insights" tab, follow-up session, 2026-08-20)

Adds a 5th dossier tab, "Insights" — an on-demand AI strategic analysis,
same "Gerar"/"Gerando..."/"Gerar novamente" UX already validated for the
suggested-approach messages (see "Suggested approach message
generation" above), but a different kind of output: not a message to
send, a structured read of the lead's overall situation for the sales
rep deciding how to prioritize/approach it.

**Backend**:

- `Lead.insights_ia` (`JSON`, nullable) — a dict with 5 keys:
  `resumo_estrategico` (string), `potencial_oportunidade` (string,
  expected "alto"/"médio"/"baixo" but **not enforced** as an enum — see
  below), `recomendacao_abordagem` (list of up to 3 strings),
  `estrategia_comunicacao` (string), `cta_sugerido` (string).
  `Lead.insights_gerado_em` (`DateTime`, nullable) — timestamp of the
  last generation. Migration: see "Migrations" below (sixth migration,
  `f80919b2f7e2`).
- `gerar_insights_estrategicos(lead: dict, client=None) -> dict` in
  `app/services/ai_enrichment.py` — same model (`claude-haiku-4-5-20251001`)
  and defensive pattern as `gerar_mensagem_abordagem` (runs inside an
  HTTP request handler, so it never lets `httpx.HTTPError` propagate nor
  raises on a malformed response — returns `{}` in either case, reusing
  the same tolerant `_extrair_json` helper `inferir_dados_site` already
  uses). What's different from `gerar_mensagem_abordagem`: the prompt
  context. `_montar_contexto_lead` (used for approach messages)
  deliberately lists only a small subset of signals, so the AI doesn't
  get distracted padding out a 2-sentence WhatsApp message with
  irrelevant detail. Insights needs the opposite — the whole picture, to
  point out what's strong/weak about *this specific* lead — so a new,
  much richer context builder, `_montar_contexto_lead_insights`, was
  added instead of extending the existing one: score total, full
  breakdown per criterion (from `score_detalhes.breakdown`), PGFN debt
  (with value and ajuizado flag, not just the count), Zona Franca, RQE
  + source, all digital-presence fields (site/Instagram/LinkedIn as a
  presence summary, not raw URLs — the AI doesn't need the links
  themselves), Google rating, `anos_mercado`, WhatsApp/e-mail validation
  status. The route feeds this via a new, separate dict-builder,
  `_montar_dados_lead_insights` in `app/api/routes/leads.py` — a
  superset of the existing `_montar_dados_lead` (which stays as-is,
  still used only by `gerar_abordagem`), not a replacement, since the
  two callers genuinely need different amounts of context.
- `potencial_oportunidade` is normalized (`.strip().lower()`) but
  **deliberately not forced** into the 3 expected values — if the AI
  responds with something else, the value passes through as-is and the
  frontend's badge falls back to a neutral/gray visual (see below)
  instead of the backend silently coercing an unrecognized value to
  "baixo", which would misrepresent what the AI actually said.
- `POST /api/leads/{lead_id}/gerar-insights` (protected, any role,
  `response_model=LeadRead`) — generates and **overwrites**
  `insights_ia`/`insights_gerado_em` (no history, unlike
  `LeadMessage` — "Gerar novamente" replaces, not appends; wasn't
  requested and doesn't fit the "single current analysis" nature of
  this feature the way per-channel message history does). 404 for a
  missing lead; 502 (same convention as `gerar_abordagem`) when
  `gerar_insights_estrategicos` returns `{}`, without persisting
  anything, so a failed generation never overwrites a previously good
  `insights_ia` with an empty result.
- `LeadRead` gained `insights_ia: dict | None` and
  `insights_gerado_em: datetime | None` — `GET /api/leads/{id}` already
  exposes both automatically (from-attributes model, no route change
  needed there).
- Tests: `tests/test_ai_enrichment.py` (function-level — success,
  `potencial_oportunidade` normalization, recommendation list capped at
  3, malformed/empty/non-JSON response → `{}`, HTTP error/network error
  → `{}`, a lead with only `razao_social` filled in doesn't crash the
  context builder) and `tests/test_leads_routes.py` (route-level —
  success + persistence, regenerate overwrites rather than appending,
  the route actually passes the richer context — not just the
  `gerar_abordagem` subset — through to the service function, 404, 502
  without persisting, 401, and that `GET` exposes both new fields
  whether or not insights were ever generated). 17 new backend tests
  total (9 + 8).

**Frontend**:

- 5th tab, `{ id: "insights", label: "Insights", icon: Lightbulb }` in
  `ABAS` (`LeadDossierPage.tsx`) — `Lightbulb` confirmed present in this
  project's `lucide-react` version (1.33) before use, same
  due-diligence habit as the `Link2`-instead-of-`Linkedin` substitution
  in the Contatos tab.
- `AbaInsights` component mirrors `CanalAbordagemCard`'s generate/
  loading/error pattern, but works on the whole `Lead` rather than a
  single `LeadMessage`: `POST /gerar-insights` returns the **entire
  updated lead** (not an isolated object), so `onGerado` receives a full
  `Lead` and the page just does `setLead(leadAtualizado)` — no manual
  merging of `insights_ia`/`insights_gerado_em` into existing state.
  Empty state (`insights_ia` is `null`): explanatory paragraph + "Gerar
  Insights com IA" button. Populated state, top to bottom: "Gerado
  {formatRelative(insights_gerado_em)}" (reusing the same relative-time
  helper already used for the header's "Atualizado há X"), the
  `resumo_estrategico` in a larger/brighter paragraph than
  `.dossier-muted` text elsewhere (explicitly asked to be "in
  destaque"), a `potencial_oportunidade` badge, "Recomendação de
  abordagem" as a bulleted list with a `CheckCircle2` icon per item,
  "Estratégia de comunicação" as plain text, and `cta_sugerido` in a
  visually distinct card. A small "Gerar novamente" button sits next to
  the tab's section heading (reuses `.dossier-abordagem-btn-secundario`,
  same small/outlined button already used for that exact label
  elsewhere).
- Badge color judgment call: verde (`#8ee6ab`, same green already used
  by `.indicador-on`/`.rqe-alta-confianca`/`layer-estruturado`) for
  "alto", **`var(--gold)` for "médio"** (not a new yellow — the
  palette is navy/gold and gold already reads as "medium/attention" in
  this UI, e.g. `.rqe-inferencia`), and the neutral gray tone
  (`.indicador-off`'s sibling) for "baixo" **and** for any unrecognized
  value — deliberately the same visual treatment for both, since an
  unexpected string from the AI isn't necessarily "low", it's
  "unclassified", and pretending otherwise would be a silent
  misrepresentation on top of the backend's own (documented) decision
  not to coerce that value either.
- CTA card treatment ("fundo diferenciado, como um call-to-action
  visual", per the request): the only block on this entire screen with
  a solid `var(--gold)` background rather than the dark-navy-card +
  gold-accent pattern everything else uses — deliberately so it reads
  as "an action to take" rather than another piece of information,
  reusing the same full-gold fill already established for primary
  action buttons (`.dossier-abordagem-btn-acao`) but applied to a whole
  card instead of a button.
- `getInsights(insightsIa: unknown): InsightsIA | null` in a new
  `frontend/src/insights.ts` — defensive runtime parsing of
  `Lead.insights_ia`, same rationale as `getScoreBreakdown` in
  `leadScore.ts` (`fetch().json()` returns `any`, so a shape mismatch
  wouldn't be caught by TypeScript at compile time — see "Lead dossier
  score breakdown bug" below for the precedent this follows). An
  unexpected shape (including a JSON array, which is never a valid
  `insights_ia` value) returns `null`, which the tab treats identically
  to "not generated yet" rather than crashing. `recomendacao_abordagem`
  is normalized to always be a string array even if the field is
  missing or the wrong type. 6 new vitest tests in
  `frontend/src/insights.test.ts`, same style as `leadScore.test.ts`.
- `npm run build` / `npm run lint` / `npm run test` all clean (18
  frontend tests total, up from 12). **Not validated in an actual
  browser** — same standing limitation as every other frontend session
  in this project; visual validation is on the user, per their explicit
  instruction this session ("sem navegador — documente decisões,
  validação visual fica comigo depois").

### Complete retheme to light mode (`frontend/`, follow-up session, 2026-08-21)

Frontend-only, every CSS file touched. Replaces the navy/gold **dark**
theme (deliberate since the first frontend session, reinforced in
"Visual polish pass" above) with a **light** theme anchored on Minotto
Contabilidade's actual brand — navy logo with a discreet gold accent,
institutional site on a white/light-gray background — per explicit
client-driven instruction. This is not a toggle/`prefers-color-scheme`
addition; the dark theme is gone, not made optional. See the amended
warning in "Visual polish pass" above — that session's "don't revisit
light vs. dark" guidance is now explicitly superseded, on record, not
silently contradicted.

**Sandbox limitation still applies**: no browser in this environment
(same standing limitation as every prior frontend session). A full
retheme is exactly the kind of change most likely to hide a legibility
bug (text-on-background contrast is invisible in the diff, only in the
render) — every color decision below is backed by a computed WCAG
contrast ratio, not eyeballed, specifically because there's no way to
eyeball it here. Treat this whole session's output as "implemented and
contrast-checked, pending a real look" — same standing caveat as every
visual session, but worth restating with extra weight here given the
scope.

**Full rename, not aliasing.** Old variable names (`--navy`,
`--navy-dark`, `--gold`, `--gold-dim`, `--gray`, `--bg`,
`--bg-elevated`, `--shadow-gold-hover`, `--bg-gradient`) don't exist
anymore anywhere in the codebase — every CSS file was individually
re-read and rewritten rule-by-rule, not just patched at the `:root`
definition. Keeping old names as aliases pointing at new hex values was
considered and rejected: a variable named `--navy` holding a light-theme
hex would permanently mislead whoever reads it next. Confirmed via
`grep -rn "var(--navy\|var(--gold-dim)\|var(--gray)\|var(--bg)\b" src`
returning nothing after the session.

**New tokens** (`frontend/src/tokens.css`, still the single color
source of truth — `index.css` now only holds font tokens + global
element styles that *consume* these variables):
- `--color-primary` (`#1b3a5c`) / `--color-primary-dark` (`#142c46`) —
  navy, now the DOMINANT color: buttons, links, active tab/nav state,
  section headings, focus rings. This is the single biggest behavioral
  change from the old theme, where gold filled that role (primary
  buttons, active states, section headings were all gold before).
- `--color-gold` (`#c9a227` — this is literally the client's exact
  requested hex, and coincidentally already matched the old theme's
  `--gold-dim` value, not `--gold`) — restricted to small
  details/badges only per explicit instruction: the Kanban-status pill,
  priority-B badge, "inferência" signal-layer tag, RQE-inferência
  badge, "potencial médio" badge, the score number's underline, and a
  4px accent border on the Insights CTA card. Nowhere does it fill a
  large area or a frequently-repeated dominant element anymore (that
  role moved to `--color-primary`) — audited by grepping every old
  `var(--gold)`/`var(--gold-dim)` usage (52 occurrences) and
  reclassifying each one individually as either "true small-badge
  accent → stays gold" or "dominant/repeated UI element → becomes
  navy," not a blanket find-replace.
- `--color-gold-dark` (`#8a6b14`) — **not requested by the client, added
  because of a contrast problem**: `--color-gold` itself is only
  2.42:1 against white (computed this session, script in the session
  transcript), below the 4.5:1 WCAG AA minimum for normal text and even
  below the 3:1 minimum for graphical objects/large text. Every place
  gold was previously used as *standalone text or icon color* on a
  light surface (not paired with a dark-text-on-gold-fill button, where
  the contrast that matters is different) now uses `--color-gold-dark`
  instead (5.01:1, passes AA) — priority-B text, RQE-inferência text,
  "inferência" layer-tag text, "potencial médio" text. `--color-gold`
  itself is still used for the score underline and CTA border (thin
  decorative lines, not text) and for pill/badge *backgrounds* (paired
  with `--color-gold-dark` text on top, not read on its own).
- `--bg-page` (`#f5f6fa`) / `--bg-card` (`#ffffff`) — page background
  vs. card surface, the light-theme equivalent of the old `--bg`/dark
  card split, same structural role.
- `--bg-elevated` (`#f3f4f6`) — form-field surface, slightly darker
  than `--bg-card` so an input has a visible boundary inside a white
  card even unfocused. Still a **solid** color, not translucent, for
  the same reason the old dark `--bg-elevated` had to be solid: the
  Chrome-autofill override trick (`LoginPage.css`) fills a `box-shadow`
  inset with this exact value, and an alpha color there would let the
  browser's own autofill color show through at the edges (this
  constraint is independent of light/dark — see "autofill" bullet
  below).
- `--text-primary` (`#1a1a2e`, 17.06:1 on white) / `--text-secondary`
  (`#6b7280`, 4.83:1) / `--text-muted` (`#9ca3af`, 2.54:1 — **below**
  AA even for large text; used only for genuinely decorative/tertiary
  text that's never the sole carrier of meaning, e.g. the `·` separator
  dot in the header's resumo line, never for a value someone needs to
  read).
- `--border` (`#e5e7eb`).
- **Recalibrated semantic colors** — same meaning as before (green =
  success/priority A, gold = warning/priority B, red = danger/priority
  C, blue = the score breakdown's "validação" signal layer), same
  request to keep the *logic* unchanged, but the actual hex values
  needed to change: the old dark-theme pastels were chosen to pop
  against a dark navy card and are unreadable as text on white —
  computed this session: old green `#8ee6ab` → 1.49:1 on white, old red
  `#f0938e`/`#e8837f` → 2.27:1/2.63:1, old blue `#a8c4f0`/`#7ea6e8` →
  1.78:1/2.47:1, all far below AA. New pairs, each a pale `-bg` wash
  (badge/panel fill) + a saturated-enough `-text` color (badge/panel
  text), same Tailwind/GitHub-label pattern used for the gold pair
  above: `--color-success` `#166534` (7.13:1) / `--color-success-bg`;
  `--color-danger` `#b91c1c` (6.47:1) / `--color-danger-bg`;
  `--color-info` `#1d4ed8` (6.70:1) / `--color-info-bg`. Badge/panel
  *borders* use a custom mid-alpha of the `-text` color's own RGB
  triplet (e.g. `rgba(22, 101, 52, 0.3)` for success), not the `-bg`
  token, so the border stays visible as a thin outline against
  `--bg-card` white — documented inline in `LeadDossierPage.css` at
  each usage rather than adding yet more root tokens for what's a
  handful of one-off border tints.
- `--overlay-primary-weak` / `--overlay-primary-medium` (`rgba(27, 58,
  92, 0.05)` / `0.1`) — replace every old `rgba(255, 255, 255, X)`
  hover/active wash. Those depended on a dark background to be visible
  at all; over `--bg-page`/`--bg-card` a white-on-white wash simply
  doesn't render. Used for sidebar nav hover/active, tab hover, chip
  hover, breakdown-row hover — anywhere a subtle "this is interactive"
  tint was needed without a full color change.
- **Shadows** recalibrated for light-theme physics, not just softened
  numbers: `--shadow-sm` `0 1px 3px rgba(0,0,0,0.08)`, `--shadow-md`
  `0 4px 12px rgba(0,0,0,0.06)`, `--shadow-lg` `0 16px 32px
  rgba(0,0,0,0.1)` — the old dark-theme shadows went up to 0.42 alpha,
  which on a white surface would read as a hard gray smear rather than
  soft elevation. `--shadow-primary-hover` (navy-tinted,
  `rgba(27,58,92,0.18)`) replaces `--shadow-gold-hover` — every button
  that used the gold hover shadow is now a navy-filled button (see
  above), so the accent shadow moved with it.
- **`--bg-gradient` removed outright, not recalibrated.** The old dark
  radial gradient doesn't have a meaningful light-theme equivalent — a
  white-to-`--bg-page` gradient would be nearly invisible and not worth
  the added CSS. `body`'s background is now a flat `--bg-page`,
  matching the explicit brief ("site institucional com fundo
  branco/cinza claro" — institutional sites are typically flat, not
  gradient-heavy).

**`color-scheme: dark` → `color-scheme: light`** (`index.css` `:root`)
— same mechanism as before (nudges browser-native chrome — scrollbars,
unstyled form controls, some autofill affordances — toward the
matching scheme), just flipped.

**Score number: glow → underline.** `.dossier-score-total`
(`LeadDossierPage.css`) — the old `text-shadow` gold glow doesn't work
on a light background (a glow needs a dark surface to read as
"luminous"; on white it just looks blurry). Replaced with `color:
var(--color-primary)` (navy, matching the explicit instruction) plus a
3px `--color-gold` `border-bottom` directly on the number — the parent
`.dossier-header-score` is a column flex container with `align-items:
flex-end`, so the `<div>` shrinks to its own content width and the
underline hugs exactly the digits, not the whole column. This was the
literal instruction ("sublinhado... sem o glow"), not a judgment call.

**CTA card (Insights tab): solid gold → solid navy + gold accent
border — a judgment call, not explicit instruction.** The client's
instructions named the CTA card as one example of "small
details/badges" being the only acceptable gold use, but didn't
literally say "recolor the CTA card." Read literally against that rule,
though: a full-width solid-fill card is not a small detail — it was the
single largest gold area in the entire dark-theme UI. Recoloring it to
`--color-primary` (matching every other button/CTA in the app, which
all moved to navy) keeps the "this is an action, not information"
visual distinction intact while staying consistent with the "gold =
small only" rule; a 4px `--color-gold` left border keeps *some* brand
gold presence on the card without it being the dominant fill. If this
reads as *too* subdued once seen for real, swapping the fill back to
gold (keeping the navy border for contrast) is a two-line CSS change,
flagged here so a future session doesn't have to re-derive the
reasoning.

**Section headings/subheadings, breakdown "points," Kanban card score,
tab-active state, sidebar-active state: gold → navy — all judgment
calls under the same rule.** None of these were named individually in
the brief; each was reclassified by asking "does this repeat often
enough, or fill enough area, to read as *dominant* rather than a
*detail*?" A `.dossier-section-heading` (icon + label) appears ~15
times across one dossier page (every card, every tab); a breakdown
"points" value appears 9 times per lead; the Kanban card score appears
once per card, dozens of times per board; the active tab/sidebar-item
fills a solid pill/bar. All reclassified as "dominant" → navy. Contrast
with what *stayed* gold: the Kanban-status pill (one per lead, small,
low-frequency-per-screen), the priority-B badge, and the three
score/RQE "inference-tier" badges — all small, low-repetition, and
literally the examples the client's own message named ("badges").

**Autofill trick adjusted, not removed** (`LoginPage.css`) — same
mechanism as the original dark-theme fix (`-webkit-box-shadow: 0 0 0
1000px var(--bg-elevated) inset` + `-webkit-text-fill-color`, see "Login
page autofill white background" fix in an earlier session), just
recolored: `--bg-elevated` is now light gray/white instead of dark
navy, and `-webkit-text-fill-color` is `var(--text-primary)` instead of
white. Worth noting the visual stakes are much lower now than when this
was first fixed: the original bug was a *stark* white flash on a dark
navy field; now our own `--bg-elevated` and the browser's default
autofill color (typically a pale yellow) are both already light, so a
skipped fix would be a subtle mismatch, not a jarring one. Kept anyway,
for the same reason as always: guaranteeing the exact chosen color
renders, not whatever the browser defaults to.

**Files touched**: `tokens.css` (full rewrite — token definitions),
`index.css` (font tokens + `body`/`:root` + `.priority-badge` family),
`components/Sidebar.css` (full rewrite — white panel, navy active
state), `pages/LoginPage.css` (full rewrite — white card, navy button,
recolored autofill trick), `pages/KanbanPage.css` (full rewrite —
white columns/cards separated by border+shadow rather than a fill-tone
difference, navy modal button), `pages/LeadDossierPage.css` (full
rewrite, largest file — header, tabs, PGFN alert panel, all 5 tabs'
card content, all badge families). `components/AuthenticatedLayout.css`
had no color rules, untouched.

**Verification**: audited every CSS file for hardcoded hex/`rgba()` not
routed through a variable (`grep -rnoE "#[0-9a-fA-F]{3,8}"` /
`rgba?\([^)]+\)` across `src/**/*.css`) both before starting (to find
what needed fixing) and after finishing (to confirm nothing dark-theme
survived) — the only hex literals remaining post-session are `#fff`
used as literal white text on a solid navy/success/danger fill (a
fixed contrast pair, not a themed surface color) and one hex mentioned
*inside a CSS comment* explaining a contrast number, not in a rule.
Also grepped every `.tsx` file for inline `style={{ color/background
}}` — none exist anywhere in this codebase, confirmed again this
session, so no color logic lives outside CSS. `npm run build` / `npm
run lint` / `npm run test` all clean (18 frontend tests, unchanged —
this was a pure CSS session, no component logic touched, so no new
tests were needed or added).

### Dashboard (Fase 1) — resumo executivo, consumo do plano, ações recomendadas, simulador de receita (follow-up session, 2026-08-21)

Adds the second sidebar item that used to be "em breve" — "Busca de
Leads" is still disabled/reserved, only "Dashboard" got built this
session. Four sections, all read from data that already existed
(`Lead`'s score/kanban/PGFN/contact fields) except the revenue
simulator's assumptions, which are new per-user state.

**Backend** (`app/api/routes/dashboard.py`, new file, mounted at
`/api/dashboard`, every route behind `get_current_user` — same
protection level as the rest of the API, not admin-only):

- `GET /summary` — `leads_no_mes` (count where `created_at >=` the 1st
  of the current UTC month), `leads_no_mes_limite` (`settings.PLANO_LEADS_MES`,
  a fixed `50` — there's no per-client/per-plan concept anywhere in this
  codebase yet, so this is a single global constant, not read from the
  `User` row), `score_medio` (`AVG(Lead.score_total)`, rounded to 1
  decimal — SQL `AVG` already skips `NULL` scores on its own, and a
  `NULL` result — no scored leads at all — is treated as `0.0`, not an
  error), `leads_em_negociacao` (`kanban_status IN
  ("proposta_enviada", "negociacao")`), `taxa_conversao` (`ganho` count
  ÷ total count × 100, rounded to 1 decimal, **`0.0` when there are zero
  leads at all** — guarded explicitly before dividing, not caught via
  exception handling).
- `GET /acoes-recomendadas` — 4 fixed categories, each its own `COUNT`
  query, returned **only when `quantidade > 0`** (a "0 leads" card
  teaches a sales rep nothing, so it's omitted rather than shown
  grayed-out): leads with `decisor_nome` set still sitting in
  `"novo_lead"`; `prioridade == "A"` leads still in `"novo_lead"`; leads
  with `decisor_nome`, `telefone`, **and** `email` all `NULL` (the
  "needs manual review" bucket — deliberately the intersection of all
  three, not any one of them, matching the literal rule as specified);
  leads with `divida_ativa_qtd_inscricoes > 0` still in `("novo_lead",
  "qualificacao")`. Each item carries `kanban_status_filtro` (a single
  status string when the rule collapses to exactly one column, `None`
  when it spans multiple or none) and `filtro_chave` (a stable slug per
  rule) — **neither is consumed by the frontend yet** (see below), both
  exist so a future session can wire a real Kanban filter without
  another backend round-trip.
- `GET`/`PUT /premissas` — the revenue simulator's 3 assumptions
  (`leads_qualificados`, `taxa_fechamento`, `ticket_medio`), persisted
  per-user in the new `User.dashboard_premissas` JSON column (see
  "Migrations" below, 7th migration). `GET` returns the saved value
  when one exists; when `dashboard_premissas is None` (never saved),
  it returns a **computed-on-the-fly default** — `leads_qualificados`
  from a real `COUNT` of today's priority-A+B leads, `taxa_fechamento`
  fixed at `20`, `ticket_medio` fixed at `1500` (both literal guesses,
  there's no historical conversion/deal-size data anywhere yet to
  derive them from) — and **never persists that default itself**; only
  `PUT` writes to the column. `PUT` always overwrites the whole object
  (`current_user.dashboard_premissas = dados.model_dump()`), no partial
  updates, no history — matches the "single current simulator state"
  nature of the feature, same non-historical pattern already used for
  `Lead.insights_ia`.
- Tests: `tests/test_dashboard.py`, 17 cases — each `summary` metric
  individually (including the zero-leads-doesn't-crash case for
  `taxa_conversao`/`score_medio`), all 4 `acoes-recomendadas` categories
  together plus an isolation case (`decisor_nome` set but the lead
  already moved out of `"novo_lead"` must NOT count), the omit-when-zero
  behavior, `premissas` default-when-unset / persist-and-reread /
  per-user-isolation (one user's saved premissas must not leak into
  another user's `GET`), and 401 coverage for all 4 endpoints. One
  fixture trap worth knowing if you touch this test file: several of the
  hand-built `Lead` rows in the "4 categories" test needed an explicit
  `telefone`/`email` set even though that test wasn't *about* contact
  info — a lead with `decisor_nome`/`telefone`/`email` all left at their
  default `None` accidentally also satisfies the "revisão manual" rule,
  double-counting into a second category and breaking the
  one-lead-per-category assertion. Not a bug in the route (the 4 rules
  really can overlap on a real lead), just something to watch for when
  hand-crafting fixture rows that are meant to isolate one category.

**Frontend** (`pages/DashboardPage.tsx` + `.css`, new; route `/dashboard`,
same `ProtectedRoute`/`AuthenticatedLayout` wrapping as every other
authenticated screen):

- **Sidebar**: the "Dashboard" item (`components/Sidebar.tsx`) changed
  from a disabled `<span>` with an "em breve" pill to a real `<Link>`,
  same active-state treatment as "Kanban" (`useLocation().pathname ===
  "/dashboard"`). "Busca de Leads" is untouched — still disabled, still
  no route, exactly as before; only "Dashboard" graduated out of "em
  breve" this session.
- **Data loading**: one `Promise.all([fetchDashboardSummary,
  fetchAcoesRecomendadas, fetchPremissas])` on mount, same pattern as
  the lead dossier's parallel `fetchLead`/`fetchMensagens` fetch — all
  three land together, one `carregando`/`erro` pair for the whole page
  (no per-section loading state; Fase 1 doesn't need it, the whole page
  is one screen's worth of data).
- **Resumo executivo**: 3 `KpiCard`s (Score médio, Leads em negociação,
  Taxa de conversão) — white card, icon in a tinted `--color-primary`
  circle, big `--color-primary` number (`Gauge`/`Handshake`/`Percent`
  from `lucide-react`, all confirmed present before use, same
  due-diligence habit as every icon choice in this project). Same
  "big numbers use navy, not gold" rule established in the retheme
  session — these are prominent, repeated-per-visit numbers, not small
  badges.
- **Consumo do plano**: `"{leads_no_mes} / {leads_no_mes_limite} leads
  este mês"` + a progress bar. Fill is `--color-primary` normally; at
  **≥90%** of the limit it switches to a **new token, `--color-alert`**
  (`#c2410c`, orange — added to `tokens.css` this session). The request
  offered "laranja/vermelho" as example colors for *one* alert
  threshold, not two separate tiers — read literally that way rather
  than inventing a second (unrequested) red tier at 100%+; `--color-danger`
  (red) stays reserved for the actual error/danger semantics already
  established elsewhere (PGFN ajuizado, etc.), not reused here. Bar
  width is `Math.min(percentual, 100)%` so it never visually overflows
  the track even if usage exceeds the plan, but the adjacent text always
  shows the real count. A one-line warning ("Limite do plano atingido"
  vs. "Perto de atingir o limite") appears only in the alert state.
- **Ações recomendadas**: one card per category from the API, icon
  chosen by `filtro_chave` (`ACAO_ICONES` map — `Landmark` for the PGFN
  category specifically reuses the same icon already used for PGFN in
  the lead dossier, same icon-per-concept convention established there;
  a `ListChecks` fallback covers any future category the frontend
  doesn't have a specific icon for yet, so a new backend category never
  breaks rendering, just shows a generic icon). Quantity is the visually
  "em destaque" element per the request — bold, `--color-primary`,
  larger than the title text next to it. **"Ver leads" link points at
  `/` (this app's actual Kanban route — there is no separate `/kanban`
  path) and does not apply any real filter yet** — explicit,
  acknowledged simplification for this phase; `kanban_status_filtro`/
  `filtro_chave` already exist on the API response specifically so a
  future session can wire a real filtered Kanban view without touching
  the backend again (see README "O que falta").
- **Simulador de receita**: 3 controlled number inputs
  (`leads_qualificados`, `taxa_fechamento`, `ticket_medio`), initialized
  from `GET /premissas`. Receita estimada
  (`leads_qualificados × (taxa_fechamento / 100) × ticket_medio`) is
  computed inline on every render from current form state — genuinely
  a one-line multiplication, not the kind of subtle/error-prone
  heuristic that earned `getScoreBreakdown`/`getInsights` their own
  pure-function + test treatment in earlier sessions, so no new
  `frontend/src/*.test.ts` file was added for it (reconsider if a bug
  report ever traces back here, same standing exception this project
  already documents for similarly simple JSX-adjacent logic). "Salvar
  premissas" follows the same `Loader2`-spinner/disabled/error-message
  pattern as every other AI-generation button on the dossier, even
  though this isn't an AI call — just the same established "loading
  async action" visual language reused for consistency, plus a
  transient "Premissas salvas." success message (`setTimeout`, 2s, same
  technique as `CampoEmail`'s copy-button feedback in the dossier).
- **CSS**: entirely new tokens already established in the retheme
  session (`--color-primary`, `--bg-card`, `--overlay-primary-weak`,
  etc.) plus the one new token this session added (`--color-alert`).
  Audited for hardcoded colors the same way the retheme session was —
  the only literal hex in `DashboardPage.css` is `#fff` (white text on
  the solid navy "Salvar premissas" button), same accepted exception as
  every other solid-fill button in this project.
- **Not tested in an actual browser** — same standing sandbox
  limitation as every other frontend session in this project.
  `npm run build`/`npm run lint`/`npm run test` all clean, 18 frontend
  tests (unchanged — see the "no new test file" note above).

### Dashboard (Fase 2) — funil de conversão, motivos de perda (follow-up session, 2026-08-21)

Adds the two remaining sections planned for the dashboard. No model
changes, no migration — both endpoints are pure aggregate queries over
columns that already existed (`Lead.kanban_status`, `Lead.motivo_perda`).

**⚠️ The funnel is a snapshot, not a cohort conversion rate — read this
before adding anything that implies otherwise.** `GET
/api/dashboard/funil` (`app/api/routes/dashboard.py::get_funil`)
reports "how many leads sit in each Kanban column right now," not "of
the leads that entered stage X, how many advanced to stage Y." This
project has no status-transition history table — `Lead` only stores
the *current* `kanban_status`, never a log of past values or when they
changed — so a real cohort/funnel-drop-off metric is **not
computable** with the data that exists today; building one would
require adding that history table first, which this session
deliberately did not do (out of scope, not forgotten). The docstring on
`get_funil` carries the same warning verbatim, specifically so a future
session skimming the code (not this doc) still hits it before adding a
"conversion rate between stages" field.

⚠️ **The paragraph below and the `GET /funil` bullet describe this
endpoint's ORIGINAL Fase 2 shape (8 stages, "perdido" excluded
entirely) — since amended, see "Funil now includes 'perdido'" further
below for the current behavior (9 stages, "perdido" included, denominator
changed).** Left as-is here as a historical record of the original
reasoning, not current behavior — don't implement against this bullet.

- `GET /funil` — the 8 non-`"perdido"` `KanbanStatus` values, **always
  all 8, including ones with `quantidade: 0`** (deliberately the
  opposite of `get_acoes_recomendadas`'s "omit zero" rule from Fase 1 —
  here a zero-count stage is itself meaningful information: "this stage
  of the pipeline is currently empty," not noise to hide). One
  `GROUP BY kanban_status` query (excluding `"perdido"` at the SQL
  level) builds a `{status: count}` dict, then the fixed-order stage
  list (`_ETAPAS_FUNIL`, mirrors `KanbanStatus`/`KANBAN_COLUMNS` order
  minus `"perdido"` — same "nothing syncs this automatically between
  the three places" caveat already noted for the other two) fills in
  `0` for any status absent from that dict — no N+1, one query total.
  `"perdido"` is excluded from **both** the numerator (it's not in the
  returned list) and the denominator (`total_nao_perdidos` sums only
  the grouped, already-`"perdido"`-excluded counts) — funnel is about
  progression, loss gets its own section per the explicit request.
- `GET /motivos-perda` — `Lead` rows with `kanban_status == "perdido"`
  and a non-empty `motivo_perda`, grouped by the **exact** string (no
  AI normalization, no fuzzy grouping — "Preço alto" and "Achou caro"
  are two separate rows today, explicitly requested this way), ordered
  by count descending, capped at 10 (`_LIMITE_MOTIVOS_PERDA`). Empty
  list (not an error) when there's no lost lead with a reason yet — the
  frontend's empty state treats that as good news, not a failure state.
- Tests: `tests/test_dashboard.py` gained 10 cases — all 8 funnel
  stages present even with several at zero, percentual computed
  correctly (including the zero-total case not dividing by zero),
  `"perdido"` never appears in the funnel response under any
  circumstance, motivos-perda groups by exact text (a near-duplicate
  human-written variant does **not** merge), ignores non-`"perdido"`
  leads and `"perdido"` leads with no reason, empty-list case, and the
  10-result cap. 297 backend tests total now (270 + 17 from Fase 1 + 10
  from this session).

**Frontend** (both sections added to the existing `DashboardPage.tsx`/
`.css` from Fase 1, same file — no new page):

- **Section order is a judgment call, not specified in the request.**
  Fase 1 built Resumo Executivo → Consumo do Plano → Ações Recomendadas
  → Simulador de Receita. This session inserted Funil de Conversão and
  Motivos de Perda **between** "Consumo do Plano" and "Ações
  Recomendadas" — reasoning: KPI numbers, then plan usage, then "what
  does the pipeline actually look like" (funnel + where it leaks),
  *then* "what should I do about it" (recommended actions), *then* a
  forward-looking projection (revenue simulator) last. If this reads
  wrong once seen for real, reordering `<SecaoFunil>`/
  `<SecaoMotivosPerda>` in `DashboardPage()`'s JSX is a two-line move,
  not a structural change.
- **Funnel**: one horizontal bar per stage (`dashboard-funil-etapa`,
  a 3-column grid — label / bar / "`quantidade · percentual%`" text),
  width set inline via `style={{ width: \`${etapa.percentual}% \`}}`
  (the one place in this project's CSS that sets a color-unrelated
  inline style — same precedent as the Fase 1 plan-consumption bar and
  the dossier's score-breakdown bars). Fill is `--color-primary` for
  every stage **except `"ganho"`, which uses `--color-success`** — a
  judgment call, not requested explicitly: marks the funnel's positive
  terminal stage green, consistent with the green-means-success
  vocabulary already established everywhere else (priority A, RQE high
  confidence, breakdown "estruturado" layer, etc.), easy to revert to a
  single flat color if it reads as inconsistent once seen. **The
  honesty note is a literal, required element, not decoration** —
  `.dashboard-funil-nota`, small `--text-muted` text below the bars,
  restates in the UI itself (not just code comments) that this is a
  current-distribution snapshot, not a historical conversion rate,
  mirroring the backend docstring's warning so the caveat travels all
  the way to whoever reads the screen, not just whoever reads the code.
- **Motivos de perda**: ranked list, same horizontal-bar visual
  language as the funnel but in `--color-danger` (loss ↔ the same
  "negative" semantic red already used for PGFN-ajuizado, priority C,
  etc.) — a numbered position badge (`dashboard-perda-posicao`, small
  red circle) plus a bar whose width is relative to **the list's own
  highest count** (`Math.max(1, ...quantidades)`), not to any total
  leads figure — ranking the reasons against each other is the point,
  not showing what fraction of anything they represent. Empty state
  (`motivos.length === 0`): a `PartyPopper` icon in `--color-success`
  green plus "Nenhum lead perdido registrado ainda — ótimo sinal!" in
  full-weight `--text-primary` (not the usual small muted
  `.dashboard-muted` treatment other empty states on this page use) —
  deliberately reads as good news worth noticing, not a bland "no data"
  placeholder, per the explicit "tom positivo" instruction.
- **`api.ts`**: `FunilEtapa`/`MotivoPerda` interfaces + `fetchFunil`/
  `fetchMotivosPerda`, same fetch-with-401-handling shape as every
  other function in that file. Both added to the page's single
  `Promise.all(...)` fetch alongside the 3 Fase 1 calls — one
  `carregando`/`erro` pair for the whole page, unchanged from Fase 1
  (no new loading granularity was needed or added).
- Icons: `Funnel` (section heading), `ThumbsDown` (section heading),
  `PartyPopper` (empty state) — all confirmed present in this project's
  pinned `lucide-react` version before use, same due-diligence habit as
  every icon choice in this codebase.
- Audited for hardcoded colors the same way every CSS-touching session
  since the retheme has been — the only literal hex remaining in
  `DashboardPage.css` after this session is still just the one `#fff`
  (white text on the solid navy button) already noted in the Fase 1
  write-up above.
- **Not tested in an actual browser** — same standing limitation as
  every frontend session in this project. `npm run build`/`npm run
  lint`/`npm run test` all clean, 18 frontend tests (unchanged — the
  bar-width math here is the same class of "too simple to need its own
  pure-function test file" as Fase 1's revenue calculation, not the
  kind of subtle heuristic that earned `getScoreBreakdown`/`getInsights`
  dedicated tests).

### Revenue simulator moved into a card + modal (`components/Modal.tsx`, `components/SimuladorReceitaModal.tsx`, follow-up session, 2026-08-21)

Frontend-only, no backend change (the `PUT`/`GET /api/dashboard/premissas`
contract from Fase 1 is untouched). Repositions the revenue simulator
from its own bottom-of-page section into a 4th "Resumo executivo" KPI
card, with the 3 editable fields moved into a modal opened from that
card — same underlying calculation and API call, different UI shape.

**First reusable modal in this project.** `components/LossReasonModal.tsx`
(Kanban's "confirm loss reason" dialog) is an older, ad-hoc
implementation — its own inline backdrop/card/click-outside-to-close
logic, styled by `.modal-*` classes that live in `pages/KanbanPage.css`
and are only available because `KanbanPage.tsx` happens to import that
stylesheet. **This session did not touch `LossReasonModal.tsx` or
`KanbanPage.css` at all** — out of scope for what was asked, and
refactoring it wasn't requested. Instead, `components/Modal.tsx` is a
new, independent, generic wrapper (`titulo`, `onClose`, `children`
props; backdrop-click and a header X button both close it; Escape also
closes it — not explicitly requested, but a one-`useEffect` addition
and the behavior every user already expects from any modal, so treated
as free rather than scope creep). **Deliberately given prefixed class
names (`hp-modal-*`), not `.modal-*`** — since Vite concatenates every
imported stylesheet into one bundle, reusing the exact `.modal-backdrop`/
`.modal-card` names `KanbanPage.css` already defines would silently
collide (both would apply to elements matching those selectors,
whichever CSS ends up later in bundle order would win in ways that are
easy to get backwards). The prefix sidesteps this without needing to
touch the older component. A future session consolidating both modals
onto the new `Modal` component is a reasonable follow-up, not done here
since it wasn't asked.

**`SimuladorReceitaModal.tsx` owns a separate draft from the card's
committed values — this is the one non-obvious design decision here,
worth understanding before changing either.** The request said the
modal's calculated value updates live as the user types, but the **top
card only updates when the user clicks "Salvar premissas."** Reading
those two sentences together rules out the naive approach (lifting the
3 form fields to `DashboardPage` state and having both the card and the
modal read the same state) — that would make the card update on every
keystroke too, before anything is saved. Instead:

- `DashboardPage` keeps `premissas: DashboardPremissas` — the last
  **saved** value, what the top card renders. It's only ever set twice:
  once from `GET /api/dashboard/premissas` on mount, and once via
  `SimuladorReceitaModal`'s `onSalvo` callback after a successful `PUT`.
- `SimuladorReceitaModal` takes `premissasAtuais` only to seed its own
  local `draft` state (`useState(premissasAtuais)` at mount) — after
  that, editing the draft never touches the parent's `premissas`.
  Closing the modal without saving (backdrop click, X, Escape, or just
  navigating away) silently discards the draft; the card underneath was
  never touched in the first place, so there's nothing to roll back.
- The modal computes its own `receitaEstimada` from `draft` for its
  live-updating display; `DashboardPage` separately computes its own
  `receitaEstimada` from `premissas` for the card. Two separate
  calculations of the same formula, deliberately — they're allowed to
  disagree while the modal is open (draft ahead of the last save), and
  that disagreement is the whole point of "the card only updates on
  save."
- This is the same "self-contained subcomponent" pattern already
  established in `LeadDossierPage.tsx` (`CanalAbordagemCard`/
  `AbaInsights` — own `useAuth()` call, own `salvando`/`erro` state, own
  API call) — `SimuladorReceitaModal` calls `salvarPremissas` itself
  rather than receiving a save callback from `DashboardPage`, so all of
  "editing → saving → loading/error state → success" lives in one file.
  On success it calls `onSalvo(salvo)` (updates the parent's committed
  `premissas`) **then** `onClose()` — in that order, so the card
  already has the new value by the time it's visible again once the
  modal closes. On failure, the modal stays open with an inline error
  (`dashboard-simulador-erro`, same class/treatment as before) so the
  user can retry without re-typing everything.

**The old `SecaoSimulador` bottom section is gone, not hidden** — the
function was deleted from `DashboardPage.tsx`, not commented out or
feature-flagged. Its 3-field-grid/result/button JSX moved essentially
verbatim into `SimuladorReceitaModal.tsx` (now reading `draft` instead
of `form`, calling the local `salvar()` instead of a prop). The CSS
classes it used (`.dashboard-simulador-grid/campo/resultado/acoes/btn/
erro`) **stayed in `DashboardPage.css`** rather than moving to a new
file — they're rendered inside `.hp-modal-corpo` now instead of inside
a `.dashboard-card`, and still make sense there (the modal card already
supplies the white background/border/shadow, so the simulator content
itself needed no restyling, just a new container). One dead rule was
removed: `.dashboard-simulador-sucesso` (the old inline "Premissas
salvas." message) — no longer needed since saving now closes the modal
immediately instead of showing an inline success line first.

**The new 4th card** (`CardReceitaEstimada`, inside `SecaoResumoExecutivo`)
— per the explicit request, visually distinct from the other 3 KPI
cards: `--color-gold-bg` background (a pale gold wash) instead of the
plain white the other 3 use, plus a gold-tinted icon circle
(`--color-gold-dark` on a stronger gold tint) instead of the navy-tinted
one every other KPI card uses. **The big number itself stays
`--color-primary` (navy), not gold** — deliberate, follows the retheme
session's established rule that important/prominent numbers use navy
regardless of a card's accent color; gold here is the card's "theme,"
not the color of the data itself. Font size for that number was reduced
from the shared `36px` to `26px` (card-scoped override,
`.dashboard-kpi-card-destaque .dashboard-kpi-valor`) since a currency
string (`"R$ 12.345,00"`) is much wider than the short numbers/percentages
the other 3 cards show — at 36px it risked overflowing the card on
narrower viewports. Below the number: the premises as a one-line
sentence (`"{leads} leads · {taxa}% conversão · {ticket} ticket médio"`,
literally the example format from the request) in `--text-secondary`,
then a small text-only "Simular cenário" button in `--color-primary`
(a link/action, so navy per the established buttons-and-links rule, not
gold) that just calls `setModalAberto(true)` in the parent.

**Not tested in an actual browser** — same standing limitation as every
frontend session in this project; particularly worth a close look here
given the draft-vs-committed-state split above is exactly the kind of
logic that's easy to get right in code review but wrong in practice
(e.g., does the modal really discard edits on backdrop-click the way
intended). `npm run build`/`npm run lint`/`npm run test` all clean, 18
frontend tests unchanged (no new pure-function logic was added — the
revenue formula itself didn't change, just where it's computed).

### Funil now includes "perdido" (`get_funil`, follow-up session, 2026-08-21)

Amends Fase 2's funnel endpoint on explicit client request. **9 stages
now, not 8** — `"perdido"` was added to `_ETAPAS_FUNIL` as the final
entry (label `"Perdido"`), and the SQL no longer filters it out of the
`GROUP BY kanban_status` query. **The snapshot-not-cohort limitation
from Fase 2 is unchanged and still applies in full** — this session
only changed what's *in* the snapshot and what the percentages are
computed against, not the fundamental "no transition history exists"
constraint. `get_funil`'s docstring was updated in place (not
superseded/replaced) to describe the current 9-stage/`"perdido"`-included
behavior, plus a second `⚠️` paragraph explaining specifically why
`"perdido"` moved from excluded to included, for anyone who finds the
old reasoning in git blame and wonders why it changed.

**The denominator change is the part most likely to surprise someone
touching this code later, so it's worth being explicit here too:**
`percentual` used to be `quantidade ÷ (total leads where kanban_status
!= "perdido") × 100`. It's now `quantidade ÷ (total leads, ALL of them,
"perdido" included) × 100`. Two concrete consequences: (1) the sum of
all 9 `percentual` values across a `GET /funil` response now closes to
~100% (previous behavior: the 8 non-"perdido" stages closed to ~100% of
a subset, which reads as misleading next to a dashboard that otherwise
talks about "all leads"); (2) `sum(quantidade for all 9 stages)` now
equals `SELECT COUNT(*) FROM leads` exactly, and also equals the
denominator `get_summary` already uses for `taxa_conversao` (`total_leads`
in that function, `COUNT(*)` with no status filter) — the two endpoints
now agree on what "the total" means, where before they quietly didn't
(`get_funil` used a "not perdido" total, `get_summary` used an "all
leads" total, and nothing surfaced that mismatch anywhere).

Implementation: `contagem_por_status` is now a plain unfiltered
`GROUP BY` (the `.where(Lead.kanban_status != _STATUS_PERDIDO)` clause
was deleted, not conditionally applied), and the local variable holding
the denominator was renamed `total_nao_perdidos` → `total_leads` to
match what it now actually contains — renaming it was deliberate, not
just AI tidiness: a future reader grepping for `total_leads` (a name
already used the same way in `get_summary`) should find this function
too, and a stale `total_nao_perdidos` name would actively mislead
someone about what's in the denominator now.

Tests (`tests/test_dashboard.py`, funnel block rewritten, net +1 test —
5 before, 6 after, 298 backend tests total): 9 stages returned (not 8);
`"perdido"` present as the **last** entry with `label == "Perdido"`
(previous test asserted the opposite — that it never appeared — deleted,
not left alongside the new one, since the two are mutually
contradictory); percentages recomputed with `"perdido"` in the
denominator (a 4-lead fixture: 2 `novo_lead` / 1 `ganho` / 1 `perdido`
→ each of those three now reads 50%/25%/25%, not the old 66.7%/33.3%/
excluded); a dedicated sum-closes-to-100 test (7-lead fixture spread
across 4 statuses including 2 `"perdido"`, asserts
`sum(percentual) == pytest.approx(100.0, abs=0.5)` — the `0.5` tolerance
covers legitimate per-stage rounding to 1 decimal each, not a loose
assertion) that also cross-checks `sum(quantidade)` against a direct
`db_session.query(Lead).count()`, i.e. the exact "total bate com a
contagem real" requirement, not just an internal-consistency check.

**Frontend**: `SecaoFunil` (`DashboardPage.tsx`) needed no data-shape
changes — it already mapped over whatever `FunilEtapa[]` the API
returned, so a 9th item just renders as a 9th bar automatically. The
one required change: `"perdido"`'s bar needed a color distinct from
both `--color-primary` (the default fill) and `--color-success`
(`"ganho"`'s existing special case) — a `_classeBarraFunil(status)`
helper replaces the old inline ternary (which only had one special case
to check, `"ganho"`; a second `? :` branch would have been unreadable)
and returns `dashboard-funil-barra-fill-perdido` for `"perdido"`, which
maps to **`--color-danger`** (red) in `DashboardPage.css` — the same
red already used one section below for "Motivos de perda," chosen
specifically for that consistency (both sections on the same page are
now talking about the same concept, loss, in the same color) rather
than introducing a third gray/red variant. The on-page honesty note
(`.dashboard-funil-nota`) was extended with a sentence explaining that
`"perdido"` is now in the total but still isn't a progress stage — the
same "the caveat needs to live on the screen, not just in code
comments" principle already established for this element in Fase 2.

### Admin panel: monthly search (`BuscaLeads`, `executar_busca_mensal`, `/api/admin/buscas`, follow-up session, 2026-08-22)

A new, **admin-only** panel that manually fires the whole prospecting
pipeline end to end — `gerar_semente_leads` (ETAPA 0) then
`process_lead_pipeline` for every CNPJ the seed finds — from a button
in the frontend, instead of only being runnable by hand (a shell/REPL
call). This is the first feature in the project gated by role, both in
the backend (`require_admin`, previously wired but unused — see the
amended note in "Authentication" above) and in the frontend (a
sidebar item + a route that a "client" user, Douglas/Sulamita, never
sees or can reach — only "admin", Caio/Vinícius).

**⚠️ Triggers real, billed API calls.** Every CNPJ in the seed runs the
full enrichment pipeline documented in "Pipeline orchestration" above
(Receita Federal, PGFN, CNES, Google Places, Firecrawl + Anthropic,
Evolution/WhatsApp, Hunter.io + ZeroBounce) — this is not a dry run or
a preview. Both the backend route's docstring and the frontend UI say
this explicitly and prominently (see the frontend section below) — per
the user's own explicit instruction to be "cuidadoso e claro" about
this, since it's the one feature in this project that spends real money
per click.

**Backend**:

- `app/models/busca_leads.py` (new) — `BuscaLeads`: `id`, `iniciado_por_id`
  (FK → `users.id`), `iniciado_em`, `concluido_em` (nullable — `None`
  means "still running or the task never got to update it"), `status`
  (`StatusBusca`: `"executando"` | `"concluido"` | `"erro"`, same
  plain-string-plus-Python-Enum convention as `Lead.kanban_status`/
  `User.role`), `total_cnpjs_encontrados`/`total_leads_processados`
  (nullable ints, filled in progressively — the first as soon as
  `gerar_semente_leads` returns, the second only at the very end),
  `erros` (nullable JSON list of strings, one `"{cnpj}: {mensagem}"`
  per CNPJ that failed — never a full traceback, and never causes the
  whole run to abort). One row per **execution**, never overwritten —
  same "history table, not a singular value" shape as `LeadMessage`,
  for the same reason: the histórico list in the frontend needs every
  past run, not just the latest.
- `app/workers/celery_app.py::executar_busca_mensal(busca_id, db=None)`
  (new task, appended at the end of the file, own dependency-graph-style
  comment block matching `process_lead_pipeline`'s) — the orchestrator
  that the individual per-CNPJ `process_lead_pipeline` task was always
  missing: something that actually loops over `gerar_semente_leads`'s
  output and calls `process_lead_pipeline` for each one (this exact gap
  was called out as unfinished business in "Pipeline orchestration"
  above — "no task loops over `gerar_semente_leads`'s output... that
  glue is still manual"; this task is that glue, finally). Sequence:
  mark `busca.status = "executando"` and commit immediately (so a
  poller sees it right away, not after the whole run finishes) → call
  `gerar_semente_leads()` (bare name, no `.delay()` — same
  call-sibling-task-directly convention as `process_lead_pipeline` uses
  for its own sibling stages) → set `total_cnpjs_encontrados` and commit
  → loop `process_lead_pipeline(cnpj, razao_social=...)` per
  estabelecimento, **each call wrapped in its own try/except** — an
  exception here is caught, logged, and appended to a local `erros`
  list as `f"{cnpj}: {exc}"`; the loop keeps going. This is one level
  "outside" `process_lead_pipeline`'s own internal resilience
  (`_rodar_etapa` catches failures *within* one CNPJ's pipeline stages,
  never letting a stage failure propagate) — this task additionally
  catches failures *of* an entire CNPJ's pipeline run (e.g. the final
  DB commit inside `process_lead_pipeline` itself raising), so a fully
  failed CNPJ doesn't take down the other 300 in the batch either.
- **Each `process_lead_pipeline(cnpj)` call gets its own DB session**
  (default `db=None`), deliberately **not** the orchestrator's own
  `db_session` for the `BuscaLeads` row — passing the same session
  across many CNPJ iterations would risk one CNPJ's failed
  transaction leaving the shared session in an aborted state that
  poisons every subsequent iteration's commits. Keeping the sessions
  separate means a failed CNPJ's session is simply discarded (closed
  without commit, in `process_lead_pipeline`'s own `finally`) while
  `executar_busca_mensal`'s own session for bookkeeping is never
  touched by that failure. This costs a few more DB connections per run
  (one per CNPJ instead of one shared) — an accepted trade-off for
  correctness, not measured/tuned for connection-pool pressure at scale.
- **Error-rate cutoff, not error-count**: `status` becomes `"erro"`
  only when **more than `_CRITERIO_ERRO_TAXA_FALHA` (50%)** of the
  seed's CNPJs failed — a handful of isolated failures (one dead
  website, one API timeout) still counts as `"concluido"`, with the
  failures visible in `erros` for the admin to read, not as a run-level
  failure. Zero CNPJs found (empty seed, or `gerar_semente_leads`
  returning its `"sem_arquivos"` shape because the RFB bulk files
  aren't in `RECEITA_FEDERAL_DADOS_ABERTOS_DIR`) also counts as
  `"concluido"` — there's no CNPJ that "failed," so the 50% rule
  vacuously doesn't trigger; the `total_cnpjs_encontrados: 0` on the
  record is itself the signal something's off, not a fabricated error
  status.
- **`gerar_semente_leads()` itself raising is handled separately from
  per-CNPJ failures**, and deliberately so: if it's not wrapped, an
  exception there (e.g., a malformed bulk file) would propagate out of
  the whole task, leaving `busca.status` stuck at `"executando"`
  forever — the frontend's poll loop would then run indefinitely with
  nothing to show for it. Caught explicitly, marks the run `"erro"`
  immediately with the exception message in `erros`, and returns early
  (never enters the per-CNPJ loop).
- `app/api/routes/admin.py` (new file, mounted at `/api/admin`, every
  route behind `require_admin` — **not** `get_current_user`): `POST
  /buscas` creates the `BuscaLeads` row (status `"executando"`), calls
  `executar_busca_mensal.delay(busca.id)` — **the first real `.delay()`
  call anywhere in this codebase** (every other task invocation in this
  project calls sibling task functions directly, in-process, for
  testability — this is the first case where async dispatch to an
  actual Celery worker is the whole point, since the route must return
  immediately rather than block on a potentially very long pipeline
  run) — then returns the row immediately (201). `GET /buscas` lists
  history, most recent first, `limit` query param (default 20, capped
  at 100, same `Query(default=..., le=...)` pattern as `GET /api/leads`).
  `GET /buscas/{busca_id}` is what the frontend polls — 404 if the id
  doesn't exist.
- **Testing `.delay()` without a real broker**: `tests/test_admin_routes.py`
  monkeypatches `app.api.routes.admin.executar_busca_mensal` (the name
  as it lives in the *route module's* namespace, per this project's
  established "monkeypatch at the call site" convention) with a small
  `_FakeTask` object exposing just a `.delay()` method that records its
  calls — confirms the route dispatches with the right `busca_id`
  without ever touching Redis/Celery. `tests/test_executar_busca_mensal.py`
  tests the task itself as a plain function call (same pattern as
  `test_process_lead_pipeline.py`) — `gerar_semente_leads` and
  `process_lead_pipeline` monkeypatched at the `celery_app` module
  level, DB via an injected in-memory SQLite session. 18 new backend
  tests total (7 task-level + 11 route-level), 316 backend tests overall.

**Frontend**:

- **`role` centralized in `AuthContext`, not just Sidebar anymore** —
  `decodeRole(token)` (the JWT-payload-only, no-signature-verification
  decode, unchanged logic) moved from living solely inside
  `components/Sidebar.tsx` into `context/AuthContext.tsx`, exposed as
  `useAuth().role`. Needed in two places now (the sidebar's conditional
  menu item, and the new `AdminRoute` guard) — centralizing avoids a
  third copy of the same decode snippet. **Still explicitly not a
  security boundary** — same caveat as before, now stated once in
  `AuthContext.tsx` instead of only in `Sidebar.tsx`: real authorization
  is `require_admin` server-side (403 for "client"), this is read-only
  UX.
- `components/AdminRoute.tsx` (new) — composes with `ProtectedRoute`
  the same way `AuthenticatedLayout` already does (two single-purpose
  wrappers nested, not one component doing both jobs): `<ProtectedRoute><AdminRoute><AuthenticatedLayout>...`.
  Redirects a non-admin to `/` (Kanban), **not** `/login` — the user
  *is* authenticated, just not authorized for this one route, mirroring
  the backend's 401-vs-403 distinction (`get_current_user` vs.
  `require_admin`) at the routing layer.
- **Sidebar**: "Busca de Leads" changed from the long-standing disabled
  "em breve" placeholder to a real `<Link to="/busca-leads">` — but
  **only rendered at all when `role === "admin"`**, not shown-disabled
  for "client" (unlike the earlier "em breve" treatment, which was
  visible-but-inert for everyone). A "client" user's sidebar simply
  doesn't have this row.
- `pages/BuscaLeadsPage.tsx` + `.css` (new):
  - **Permanent, always-visible warning banner** (`.busca-leads-aviso`,
    `--color-danger`/`--color-danger-bg`) above the trigger button,
    listing every external system the pipeline actually calls, plus a
    **confirmation modal** (reusing `components/Modal.tsx`, the generic
    modal from the "Revenue simulator" session) that must be confirmed
    before the `POST` actually fires — two layers, not one, given the
    user's explicit "seja cuidadoso e claro" instruction for this
    specific feature. `--color-danger` was chosen over `--color-alert`
    (the orange token from the Dashboard Fase 2 session, meant for
    "approaching a plan limit") — real production API spend reads as
    more serious than a soft usage warning, closer to the danger-tier
    treatment already used for PGFN-ajuizado elsewhere in the app.
  - **The trigger button itself stays the standard primary-navy CTA
    style**, not styled as a "dangerous" red button — a judgment call:
    the action itself is constructive (it creates/updates leads), not
    destructive, so a red "delete"-style button would send the wrong
    signal; the caution lives in the banner and the confirmation step,
    not in making every primary action on this page look alarming.
  - **While `status === "executando"`: a spinner + "Buscando leads..."
    text, explicitly with NO percentage or fake progress bar** — per
    the explicit "seja honesto, não invente uma barra de progresso
    falsa" instruction. The only granular detail shown is
    `total_cnpjs_encontrados` once `gerar_semente_leads` has finished
    (before that, it says "Levantando a lista de CNPJs..."), because
    that's genuinely known at that point — everything past it (which
    CNPJ is currently processing, how many remain) genuinely isn't,
    without polling `process_lead_pipeline`'s internals, which this
    session didn't add.
  - **Polling**: chained `setTimeout` (not `setInterval`) every
    `INTERVALO_POLLING_MS` (4000ms) — deliberately recursive-timeout
    rather than a fixed-interval timer, so a slow response never causes
    two in-flight polls to overlap; the next poll is only scheduled
    after the current one resolves. Stops on its own once `status`
    leaves `"executando"`. A network error *during* polling (not a
    real backend error) doesn't give up — it just retries on the next
    tick, since the actual `BuscaLeads` row on the server is unaffected
    by a client-side polling hiccup.
  - **Resumes tracking after a page reload**: on mount, `GET /buscas`
    (the history list) is checked for any row still `"executando"` — if
    one exists (e.g. the admin reloaded mid-run), polling resumes on it
    automatically instead of leaving the admin with no visibility until
    it finishes on its own.
  - **On conclusion**: a result panel (`ResumoBusca`) — green/red per
    `status`, counts of CNPJs found vs. leads processed vs. errors, and
    a `<details>`/`<summary>` (native HTML disclosure, no extra JS) list
    of every individual error message when `erros.length > 0`.
  - **History section**: every row from `GET /buscas`, a status pill
    (navy/`"executando"`, green/`"concluido"`, red/`"erro"` — same
    three-color vocabulary as the result panel) plus the same
    CNPJs/processed/erros counts. Timestamp uses a plain
    `Date.toLocaleString("pt-BR")` inline rather than adding a new
    shared formatter to `format.ts` — a one-off need (date *and* time,
    unlike `formatDate`'s date-only), not worth generalizing yet.
  - `api.ts` gained `BuscaLeadsRegistro` (mirrors `BuscaLeadsRead`) and
    `dispararBusca`/`fetchBuscas`/`fetchBusca`, same
    fetch-with-401-handling shape as every other function in the file.
- **Not tested in an actual browser** — same standing sandbox
  limitation as every frontend session in this project; particularly
  worth checking here given how much of this page's behavior is
  timing/state-machine-shaped (polling start/stop, reload-resume,
  confirm-then-fire) rather than static layout. `npm run build`/`npm
  run lint`/`npm run test` all clean, 18 frontend tests unchanged (no
  new pure-function logic — this session's frontend work is
  state/effects/API wiring, not the kind of standalone parsing logic
  `leadScore.ts`/`insights.ts` earned tests for).

### Duplicate-launch lock on `POST /api/admin/buscas` (follow-up session, 2026-08-22)

Closes the "nothing prevents two searches running at once" gap flagged
as a known limitation in the admin panel session above (README "O que
falta", item 20) — but only the specific case actually asked for
(double-click / two admins both hitting "Executar busca do mês" around
the same time), not full concurrency-safety.

- `disparar_busca` (`app/api/routes/admin.py`) now does one extra
  `SELECT` — any `BuscaLeads` row with `status == "executando"` — before
  creating a new row. If one exists, **409 Conflict**, with a
  human-readable `detail` built from the existing row's own
  `iniciado_em` (`"Já existe uma busca em andamento, iniciada em
  DD/MM/AAAA HH:MM."`) — no new row is created, `executar_busca_mensal.delay(...)`
  is never called.
- **Explicitly documented as best-effort, not a real concurrency
  guarantee** — the docstring says so directly: this is a plain
  `SELECT` then `INSERT`, not a `SELECT ... FOR UPDATE` or a DB-level
  unique constraint (e.g. a partial unique index on `status =
  'executando'`). Two requests landing in the same instant could both
  pass the `SELECT` before either commits, in principle creating two
  `"executando"` rows anyway. Good enough for the real scenario this
  was asked to solve (a person clicking twice, or two admins on the
  same screen within normal human reaction time) — not hardened against
  a genuine race, which would need a DB-level constraint if it ever
  matters in practice. Don't read the 409 behavior as "this is now
  safe under concurrency" if extending this route later.
- Tests (`tests/test_admin_routes.py`, +3, 319 backend tests total):
  a second `POST` while one `BuscaLeads` is `"executando"` returns 409,
  includes the formatted timestamp in `detail`, never calls
  `.delay()`, and never creates a second row (`BuscaLeads` count stays
  at 1); a parametrized case (`StatusBusca.CONCLUIDO` /
  `StatusBusca.ERRO`) confirms a **new** search can be created normally
  once the previous one has actually finished, either way.
- **Frontend**: `api.ts` gained `ConflictError` (mirrors
  `UnauthorizedError` — a typed exception `dispararBusca` throws
  specifically on a 409, carrying the backend's already-friendly
  `detail` string as `.message`, so the page never has to compose its
  own copy of the same sentence). `BuscaLeadsPage.tsx`'s `executarBusca`
  catches it separately from the generic-failure branch: shows the
  message in a **new, distinct state** (`mensagemInfoDisparo`,
  `--color-primary` navy text, not `--color-danger` red) — deliberately
  not styled as an error, since "someone already started a search" is
  informational, not a failure the user caused. It then also calls the
  same data-fetching helper the mount effect uses
  (`buscarHistoricoEEmAndamento`, refactored out of the original
  reload-resume logic specifically so both call sites could share it)
  to pick up the *actual* in-progress `BuscaLeads` row and start polling
  it — this is what makes "Executar busca do mês" disappear (replaced
  by the existing progress panel) after a 409, reusing the
  already-built polling/tracking machinery rather than adding a
  separate disabled-state flag. **A lint-driven refactor worth knowing
  about**: the first version of this shared helper called `setState`
  directly inside itself and was invoked from a `useEffect` — oxlint's
  `set-state-in-effect` rule flagged it (a locally-defined function
  calling multiple `setState`s, invoked directly in an effect, reads
  the same as calling them synchronously even though the actual state
  updates happen inside a resolved promise). Fixed by having the shared
  helper only *fetch and return* data, with each call site (the mount
  effect, and the 409 handler) doing its own `.then()`-committed
  `setState` calls — slightly more repetition, but keeps the "effects
  own their own state commits" shape the rest of this file (and the
  rest of the project) already follows.

### "Lista de Leads" screen (`GET /api/leads/lista`, `ListaLeadsPage.tsx`, follow-up session, 2026-08-22)

A new, **additive** table view of leads — search, filters, sortable
columns, pagination — as a distinct sidebar item/route from the
existing Kanban board. No model change, no migration; this is a new
read endpoint plus a new frontend page.

**Backend**:

- `GET /api/leads/lista` (`app/api/routes/leads.py::listar_leads_paginado`)
  is a **separate, additive route from `list_leads`/`GET /api/leads`
  — deliberately not a refactor or a shared-implementation of it.**
  `GET /api/leads` keeps serving the Kanban board exactly as before
  (bare list, no pagination, no text search — the Kanban loads
  everything at once for its 9 fixed columns, pagination wouldn't make
  sense there). This was explicit in the request, and confirmed by
  running `tests/test_leads_routes.py` (the pre-existing suite,
  untouched) in full — all 34 cases still pass, including one added
  this session (`test_get_leads_antigo_continua_bare_list_sem_paginacao`
  in the *new* test file) that explicitly asserts `GET /api/leads`
  still returns a bare JSON array, not the new `{items, total, pagina,
  por_pagina}` envelope.
- **Registered before `GET /{lead_id}` in the router, not after** — this
  matters for correctness, not just style: Starlette/FastAPI route
  matching for a router is order-sensitive between a static path
  segment and a dynamic one. If `/lista` were registered *after*
  `/{lead_id}`, a request to `/api/leads/lista` would match
  `/{lead_id}` first (treating `"lista"` as a lead id) and never reach
  the new route at all. `listar_leads_paginado` sits between
  `list_leads` and `get_lead` in the file specifically so this doesn't
  happen.
- `busca` filters `razao_social` OR `nome_fantasia` OR `cnpj`
  containing the text, via SQLAlchemy `.ilike()` on all three
  (`or_(...)`) — compiles to Postgres's native `ILIKE` in production and
  to a portable `lower(...) LIKE lower(...)` on SQLite in tests, no
  dialect branching needed in the route itself.
- **⚠️ Known SQLite-vs-Postgres divergence, hit and documented this
  session (not a bug in the endpoint)**: SQLite's built-in `lower()` is
  **ASCII-only** — `"CLÍNICA".lower()` in SQLite leaves the `Í`
  untouched, so a test asserting case-insensitive search against an
  accented word would pass against real Postgres (which case-folds
  Unicode correctly) but fail in the SQLite-backed test suite for a
  reason that has nothing to do with the endpoint's actual correctness.
  `tests/test_leads_lista_route.py::test_busca_case_insensitive`
  deliberately tests case-insensitivity using only ASCII text for this
  reason, with both the test's docstring and the route's own docstring
  cross-referencing this note — same "call out the SQLite/Postgres gap
  explicitly instead of hiding it" convention already established
  elsewhere in this project (`DISTINCT ON` avoidance in
  `listar_mensagens`, the 107-byte Unix-socket-path lesson in the
  migration write-ups, etc.).
- `ordenar_por` (`"score_total"` | `"created_at"`) and `ordem`
  (`"asc"` | `"desc"`) are validated against closed sets — 422 outside
  them, same explicit-validation convention already used for
  `kanban_status` in `update_lead_status`, rather than either letting
  an arbitrary column name reach the SQL layer or silently falling back
  to a default without telling the caller.
- `total` is computed via `select(func.count()).select_from(stmt.subquery())`
  on the **filtered-but-unpaginated** query — the count of everything
  matching the current filters, not the total row count of the whole
  `leads` table and not just the current page's size. Pagination itself
  is a plain `.offset((pagina - 1) * por_pagina).limit(por_pagina)` on
  top of the same filtered/ordered statement.
- `app/schemas/lead.py` gained `LeadListaResponse` (`items:
  list[LeadRead]`, `total`, `pagina`, `por_pagina`) — `items` reuses the
  existing `LeadRead` schema wholesale (the full lead, same shape every
  other lead-returning endpoint already uses), not a slimmer
  table-specific schema, since the frontend table needs a wide enough
  slice of fields (contact-icon booleans, score, kanban_status,
  decisor, município/UF, created_at) that a trimmed-down schema
  wouldn't have saved much.
- Tests: `tests/test_leads_lista_route.py`, new file, 17 cases — search
  by partial name/nome_fantasia/CNPJ, the ASCII-only case-insensitivity
  case above, no-match-returns-empty, sort in both directions on both
  columns (including a case confirming `created_at` sorting genuinely
  reads that column, not just re-testing `score_total`), both invalid-
  value 422s, pagination (total count, page size, disjoint item sets
  across pages, out-of-range `por_pagina`/`pagina` both 422), combined
  filters (busca + prioridade + kanban_status all applied together,
  with fixture leads specifically designed so each individual filter
  alone would *not* isolate the expected result — only the combination
  does), 401 coverage, and the `GET /api/leads` regression check. 336
  backend tests total (319 + 17).

**Frontend**:

- New sidebar item, **"Lista de Leads" — deliberately a different
  concept and a different icon from "Busca de Leads"**, which already
  existed (the admin-only monthly-pipeline-trigger panel from an
  earlier session). The two are easy to conflate by name alone: "Lista
  de Leads" is a read-only table view of leads that already exist,
  available to any role; "Busca de Leads" fires the real enrichment
  pipeline, admin-only. `List` icon (not `Search`, already used by
  "Busca de Leads") specifically to keep them visually distinct in the
  sidebar, not just distinct by label. Positioned right after "Kanban"
  in the nav — visible to every role, same protection level as `GET
  /api/leads/lista` itself (`get_current_user`, not `require_admin`).
- Route `/leads` (`pages/ListaLeadsPage.tsx`) — chosen specifically to
  not collide with the pre-existing `/leads/:id` dossier route. React
  Router v6 ranks routes by specificity regardless of registration
  order (unlike the backend's Starlette router, where order genuinely
  matters — see above), so `/leads` and `/leads/:id` coexist safely
  either way, but the route was still added right next to `/leads/:id`
  in `App.tsx` for readability.
- **Debounced search** (400ms, `DEBOUNCE_BUSCA_MS`) — the input's raw
  value (`buscaInput`) is separate from the value actually sent to the
  API (`busca`); a `useEffect` on `buscaInput` schedules a
  `setTimeout` that commits `busca` (and resets `pagina` to 1) only
  after the user stops typing, clearing the previous timeout on every
  keystroke so only the last one ever fires.
- **Sortable column headers** (Score, Encontrado em) — clicking a
  header that's already the active sort flips `asc`/`desc`; clicking a
  *different* column switches to it and resets to `desc`. An
  `ArrowUpDown` icon (muted) marks sortable-but-inactive columns,
  `ArrowUp`/`ArrowDown` (full color) marks the active one in its
  current direction — so the sort state is visible without needing to
  read the URL or open dev tools.
- **A `useEffect`-driven lint fix worth knowing about, given it looks
  like it could regress**: an earlier version of this page had a
  *separate* `useEffect` whose only job was `setPagina(1)` whenever
  `busca`/`prioridade`/`kanban_status`/`ordenar_por`/`ordem` changed.
  oxlint's `set-state-in-effect` rule flagged it — a reactive effect
  that exists purely to call one `setState` in response to other state
  changing is exactly React's own documented anti-pattern ("adjusting
  state when a prop/state changes"), and the docs' own recommended fix
  is to set that state directly inside the event handler that causes
  the change instead. Refactored accordingly: the debounce timeout
  callback now sets `pagina` to 1 alongside `busca`, and the priority/
  kanban-status `<select>` `onChange` handlers and `alternarOrdenacao`
  each reset `pagina` directly rather than relying on a reactive effect
  to notice the change afterward. **One `set-state-in-effect` warning
  remains, accepted deliberately**: the main data-fetching effect
  (`useEffect` depending on `[token, logout, busca, prioridade,
  kanban_status, ordenar_por, ordem, pagina]`) still calls
  `setCarregando(true)`/`setErro(null)` synchronously at its top before
  fetching — this is the standard, React-docs-endorsed data-fetching
  pattern (unlike `DashboardPage`/`BuscaLeadsPage`, whose fetch effects
  only run once on mount, this one legitimately needs to re-fire and
  reset loading/error state on every filter/sort/page change, so there
  was no clean way to move it into a single event handler — several
  different handlers can trigger it). Treated the same way this project
  already treats the pre-existing `AuthContext.tsx` fast-refresh
  warning: a known, reviewed, accepted exception, not something to
  chase away with an awkward workaround.
- **Table columns**: Nome (razão social bold + nome fantasia smaller
  underneath, only when present), CNPJ (monospace), Contato (two plain
  icons — `MessageCircle`/`Mail` — colored `--color-success` or
  `--text-muted`, **never the actual phone number or email text**, per
  the explicit request; gated on the *confirmed* signal
  (`whatsapp_ativo`/`email_validado`), same "confirmed, not just
  present" rule already established for `CampoWhatsapp`/`CampoEmail` in
  the lead dossier), Decisor, Município/UF, Score (`PriorityBadge` +
  the raw number), Etapa (a pill reusing the dossier's established
  gold/red Kanban-status color language — gold for every status except
  `--color-danger` red specifically for `"perdido"`), Encontrado em
  (`formatDate`, existing shared formatter). Clicking a row navigates to
  `/leads/{id}` (the existing dossier), same `useNavigate()` pattern
  `KanbanCard` already uses for the same purpose.
- **Not tested in an actual browser** — same standing sandbox
  limitation as every frontend session in this project. `npm run
  build`/`npm run lint`/`npm run test` all clean (one accepted
  `set-state-in-effect` warning, explained above; 18 frontend tests
  unchanged — no new pure-function logic, this session's frontend work
  is table/filter/pagination UI wiring).

### Closing data on "Ganho" (`servicos_vendidos`/`tipo_contrato`/`valor_fechamento`, `FechamentoModal.tsx`, follow-up session, 2026-08-23)

Captures deal-closing data when a lead is dragged to the "Ganho" Kanban
column — explicitly the mirror image of the existing "Perdido"/
`motivo_perda` pattern (a modal blocks the move until the required
data is entered, backend validates conditionally on the target
status). No new model, no new table — three nullable columns on the
existing `Lead`.

**Backend**:

- `app/models/lead.py` gained `servicos_vendidos` (`JSON`, a list of
  strings), `tipo_contrato` (`String(20)`), `valor_fechamento`
  (`Float`) — all nullable, placed right after `motivo_perda` in the
  "Kanban" field block, with a comment explicitly framing them as its
  positive-side mirror.
- **`servicos_vendidos` has no closed enum at the database or Pydantic
  level, on purpose** — the request was explicit that the 4 fixed
  values ("abertura_legalizacao", "contabilidade_consultiva",
  "consultoria_empresarial", "planejamento_tributario") plus arbitrary
  free text (from the frontend's "Outro" field) all need to fit in the
  same list, and that validating the fixed-vs-free-text distinction is
  the *frontend's* job, not the backend's. The column is just
  `JSON` holding `list[str]`; the route only checks the list is
  non-empty, never what's in it.
- `tipo_contrato`, unlike `servicos_vendidos`, **is** validated against
  a closed set (`_TIPOS_CONTRATO_VALIDOS = {"pontual", "recorrente"}`)
  in the route — same explicit-validation convention as
  `kanban_status`/`ordenar_por` elsewhere in this file, since unlike
  "which services were sold" (open-ended by nature), "what kind of
  contract is this" genuinely only has two valid values worth
  rejecting bad input against.
- `PATCH /{lead_id}/status` (`update_lead_status`) — when the target
  `kanban_status` is `"ganho"`, now requires all three fields (422 with
  a field-specific message if any is missing/invalid), the exact same
  shape as the pre-existing `"perdido"`/`motivo_perda` check just above
  it in the function. `valor_fechamento` must be `> 0`, not just
  present — a `0` or negative closing value is rejected the same as a
  missing one.
- **Deliberately does NOT clear closing data when a lead is moved out
  of "ganho" later** — this is the one place this feature's behavior
  genuinely diverges from the `motivo_perda` pattern it's modeled on,
  and it's worth understanding why, not just copying the precedent
  blindly. `motivo_perda` gets cleared on exit from `"perdido"` because
  it's explaining *why something didn't happen* — if the lead isn't
  lost anymore, that reason is stale. `servicos_vendidos`/
  `tipo_contrato`/`valor_fechamento` describe a sale that *did* happen
  — moving the lead again later (a misclick, a later reclassification)
  doesn't retroactively un-sell the contract, so the historical record
  stays. The route's docstring states this explicitly, and a dedicated
  test (`test_mover_lead_ganho_pra_outra_etapa_nao_apaga_dados_de_fechamento`)
  confirms it — move to `"ganho"` with data, then move to
  `"negociacao"`, and the three fields are still there in the response.
- Tests (`tests/test_leads_routes.py`, +12, 347 backend tests total):
  every individual missing/invalid field (no data at all, missing
  `servicos_vendidos`, empty list specifically — distinct from missing
  entirely, since Pydantic treats `[]` and absent differently — missing
  `tipo_contrato`, invalid `tipo_contrato` value, missing
  `valor_fechamento`, zero and negative `valor_fechamento` both), the
  happy path with multiple services persisting correctly, free-text
  "Outro" entries surviving alongside fixed keys in the same list
  without any backend-side rejection, the no-auto-clear-on-exit case
  above, and confirmation that moving to a non-"ganho"/non-"perdido"
  status still requires neither `motivo_perda` nor any of the three
  closing fields.

**Frontend**:

- `components/FechamentoModal.tsx` (new) — built on the generic
  `Modal` component (from the "Revenue simulator" session), same
  structural role as `LossReasonModal` for the "Perdido" column but
  **not** built on the older ad-hoc `.modal-*` implementation
  `LossReasonModal` uses — this is the second component (after
  `SimuladorReceitaModal`) to use the generic `Modal`, `LossReasonModal`
  itself still hasn't been migrated (still an open, explicitly-flagged
  follow-up from the session that introduced `Modal`).
- `frontend/src/servicosFechamento.ts` (new, small shared module) —
  `SERVICOS_FECHAMENTO_FIXOS` (the 4 fixed key/label pairs) and
  `labelServicoFechamento(valor)` (translates a key back to its label,
  or returns free text unchanged since it won't match any fixed key).
  Extracted into its own file specifically because it's needed in two
  places that would otherwise duplicate the same 4-item list:
  `FechamentoModal.tsx` (building the checkboxes) and
  `LeadDossierPage.tsx` (displaying the saved selection back as
  readable labels, not raw keys, in the new "Fechamento" section).
- **Checkbox/radio styling uses `accent-color`, not a custom-built
  control** — a one-line CSS property (`accent-color: var(--color-primary)`,
  broadly supported in every current browser) recolors the native
  checkbox/radio to match the brand without building a custom
  component from scratch. First use of native form controls beyond
  plain text/number inputs and `<select>` in this project — a
  reasonable, low-effort choice given nothing about these controls
  needs custom behavior beyond looking on-brand.
- **Confirm button disabled state**: requires at least one selected
  service (either a fixed checkbox or "Outro" with non-empty trimmed
  text — checking `outroMarcado && outroTexto.trim().length > 0`, not
  just the checkbox state, so ticking "Outro" without typing anything
  doesn't count), a chosen contract type, and a valid positive number
  in the value field — mirrors the backend's own validation rules
  client-side, so the 422 case is rare in practice (only reachable via
  a stale/tampered request), not the primary way validation is
  communicated to the user.
- **Value field label flips dynamically** ("Valor único (R$)" vs.
  "Valor mensal (R$)") based on the selected contract type, exactly as
  requested — defaults to the "único" wording before a contract type is
  chosen, a minor cosmetic default since the Confirm button stays
  disabled either way until one is actually selected.
- `KanbanPage.tsx`: `handleDragEnd` gained a second early-return branch
  (`novoStatus === STATUS_GANHO`, mirroring the existing `STATUS_PERDIDO`
  branch immediately above it) that opens `FechamentoModal` instead of
  applying the move directly — same `pendingLoss`/`pendingWin` pair of
  local state, same "cancel never touches `leads` state, so the card
  visually stays put" guarantee `LossReasonModal` already had.
  `aplicarNovoStatus` gained an optional `dadosFechamento` parameter,
  spread into both the optimistic local update and the actual
  `updateLeadStatus` API call. `kanbanStatuses.ts` gained a
  `STATUS_GANHO` export (mirroring the pre-existing `STATUS_PERDIDO`)
  specifically so this comparison didn't need a bare `"ganho"` string
  literal in `KanbanPage.tsx`.
- `api.ts`: `Lead` gained the three new optional fields;
  `updateLeadStatus` gained an optional 5th parameter,
  `dadosFechamento?: DadosFechamento` (a new exported interface
  mirroring the backend's validation shape), spread into the request
  body alongside `kanban_status`/`motivo_perda` — when absent (every
  non-"ganho" call site), the spread contributes nothing, so existing
  callers didn't need to change.
- **Dossier**: a new `SecaoFechamento` component in the "Dados" tab,
  rendered *only* when `kanban_status === "ganho"` **and** all three
  fields are actually present (a defensive `&&` chain, not expected to
  ever partially fail in practice since the backend requires all three
  together — but cheap insurance against an old/malformed row).
  Deliberately minimal per the explicit instruction ("não precisa ser
  elaborado, só exibir a informação de forma legível") — a single
  `dossier-card` with three `Campo` rows (services joined as a
  comma-separated readable string via `labelServicoFechamento`,
  contract type as "Recorrente"/"Pontual", and the value via the
  existing `formatCurrencyBRL`, under a dynamic "Valor mensal"/"Valor
  único" label matching the modal's own wording) — explicitly not the
  "rich/analytical display" the request named as out of scope for this
  session. Given a light green tint (`--color-success-bg`/
  `--color-success`, the same "positive signal" vocabulary already used
  elsewhere on this page — `indicador-on`, `rqe-alta-confianca`) so it
  reads as a "won" card distinct from the page's other neutral data
  cards, positioned as a standalone card below the existing 3-card grid
  (not inside it) since it doesn't apply to most leads.
- **Not tested in an actual browser** — same standing sandbox
  limitation as every frontend session in this project. `npm run
  build`/`npm run lint`/`npm run test` all clean (same 2 pre-existing
  accepted warnings — `AuthContext.tsx`'s fast-refresh warning and
  `ListaLeadsPage.tsx`'s `set-state-in-effect` warning — no new ones;
  18 frontend tests unchanged, no new pure-function logic).

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

A **sixth** migration was added 2026-08-20 (same day, a further
follow-up session): `f80919b2f7e2_adiciona_insights_ia_e_insights_...py`
(`down_revision="c47a9e2b1d05"`, adds `Lead.insights_ia`/
`Lead.insights_gerado_em` — see "AI-generated strategic insights"
below). Same throwaway-cluster validation as the previous five (port
5433, short socket dir per the 107-byte lesson above): all six
migrations upgrade cleanly in chain order, `downgrade -1` reverts only
this one, re-upgrade is clean, and the resulting `leads.insights_ia`
(`json`)/`leads.insights_gerado_em` (`timestamp without time zone`)
columns match `app/models/lead.py` exactly — both nullable, no default
(same reasoning as `mensagem_abordagem`/PGFN columns: `NULL` already
means "not generated yet", no backfill needed). Cluster stopped and
deleted immediately after.

A **seventh** migration was added 2026-08-21 (follow-up session):
`99454b995104_adiciona_dashboard_premissas_em_users.py`
(`down_revision="f80919b2f7e2"`, adds `User.dashboard_premissas` — see
"Dashboard (Fase 1)" below). First migration to touch `users` since the
baseline — a single nullable JSON column, no default (`NULL` means "user
never saved the revenue simulator's assumptions yet", same reasoning as
every other nullable-JSON-no-default column in this project). Same
throwaway-cluster validation as the previous six (short socket dir per
the 107-byte lesson above): all seven migrations upgrade cleanly in
chain order, `downgrade -1` reverts only this one, re-upgrade is clean,
and the resulting `users.dashboard_premissas` (`json`, nullable, no
default) matches `app/models/user.py` exactly. Cluster stopped and
deleted immediately after.

An **eighth** migration was added 2026-08-22 (follow-up session):
`a8017825fae8_cria_tabela_buscas_leads.py` (`down_revision="99454b995104"`,
creates the `buscas_leads` table for `BuscaLeads` — see "Admin panel:
monthly search" below). A new table, not columns on an existing one —
same reasoning as `lead_messages`: one row per busca *execution*
(history), not a singular per-user/per-lead value. `iniciado_por_id` is
a FK to `users.id` with an index (same convention as
`lead_messages.lead_id`). Same throwaway-cluster validation as the
previous seven: all eight migrations upgrade cleanly in chain order,
`downgrade -1` reverts only this one, re-upgrade is clean, and the
resulting `buscas_leads` schema (FK to `users.id`, index on
`iniciado_por_id`, `status` `NOT NULL` `varchar(20)`, everything else
nullable with no default) matches `app/models/busca_leads.py` exactly.
Cluster stopped and deleted immediately after.

A **ninth** migration was added 2026-08-23 (follow-up session):
`a1139438a443_adiciona_dados_de_fechamento_em_leads.py`
(`down_revision="a8017825fae8"`, adds `Lead.servicos_vendidos`/
`Lead.tipo_contrato`/`Lead.valor_fechamento` — see "Closing data on
'Ganho'" below). Three nullable columns on `leads`, no `server_default`
— same reasoning as `motivo_perda`/`insights_ia`: `NULL` already means
"this lead hasn't closed as won yet," no backfill needed. Same
throwaway-cluster validation as the previous eight: all nine
migrations upgrade cleanly in chain order, `downgrade -1` reverts only
this one, re-upgrade is clean, and the resulting `leads.servicos_vendidos`
(`json`)/`leads.tipo_contrato` (`varchar(20)`)/`leads.valor_fechamento`
(`double precision`) columns match `app/models/lead.py` exactly — all
three nullable, no default. Cluster stopped and deleted immediately
after.

### Deployment note (EasyPanel)

Backend, Celery worker, and frontend are deployed as **separate App
Services** — Docker Compose multi-service setup is not well supported on
EasyPanel (lesson carried over from the SR Contábil project). The frontend's
nginx must point at the backend's public domain, not an internal Docker
hostname.
