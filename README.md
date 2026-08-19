# HunterPro — Minotto Contabilidade

Motor de prospecção ativa da 4Hands AI, parametrizado para o nicho de
**clínicas e consultórios médicos** (qualquer especialidade com RQE),
cliente Minotto Contabilidade (Douglas Minotto).

Este repositório é o ponto de partida do projeto — estrutura, modelo
de dados e motor de score já validados. O pipeline de busca/
enriquecimento em si (cada fonte de dado) é construído sessão a sessão.

---

## Arquitetura

Mesmo padrão já validado no projeto HunterPro SR Contábil:

```
backend/          FastAPI + PostgreSQL + Celery + Redis
  app/
    api/routes/    endpoints da API
    core/          config (.env) e conexão com banco
    models/        modelos SQLAlchemy (Lead, ...)
    schemas/       schemas Pydantic (serialização)
    scoring/        regras de pontuação e fontes específicas do nicho
    workers/        tasks do Celery (pipeline de enriquecimento)
  alembic/          migrations do banco
  tests/            testes automatizados

frontend/         React + Vite + TypeScript
  src/
    api.ts          cliente HTTP pro backend
    App.tsx          tela inicial (placeholder da tabela de leads)
```

**Deploy (EasyPanel):** backend, worker (Celery) e frontend como **App
Services separados** — Docker Compose multi-serviço não é bem suportado
no EasyPanel (lição já registrada do projeto SR). O nginx do frontend
precisa apontar pro domínio público do backend, não pro hostname
interno do Docker.

---

## O que já está pronto

- ✅ Estrutura de pastas do backend e frontend
- ✅ Modelo de dados do Lead (`app/models/lead.py`) — campos padrão +
  campos específicos do nicho de saúde e da Minotto
- ✅ **Motor de lead score** (`app/scoring/rules.py`) — os 9 critérios e
  pesos fechados com o Douglas em 10/08/2026, com teste automatizado
  garantindo que a soma sempre bate 100
- ✅ Lookup de **Zona Franca/ALC** (`app/scoring/zona_franca.py`) — já
  funcional, sem dependência externa
- ✅ **Parser dos Dados Abertos da PGFN** (`app/services/pgfn.py`) —
  lê o CSV trimestral, filtra pessoa jurídica e inscrições realmente
  ativas, indexa por CNPJ. Separado em duas etapas: `refresh_pgfn_index`
  (pesado, roda quando o arquivo trimestral atualiza) e
  `enrich_pgfn_divida_ativa` (consulta rápida via Redis, por lead).
  Testado com CSV sintético + Redis real rodando de ponta a ponta.
- ✅ **Fórmula da dívida ativa PGFN confirmada e implementada**
  (`app/scoring/rules.py::pgfn_points`) — Douglas validou por WhatsApp
  em 19/08/2026 ("sim, era isso mesmo"). `enrich_pgfn_divida_ativa` já
  retorna `pontos_score` calculado, não só a contagem bruta.
- ✅ API FastAPI com endpoint de listagem de leads (`/api/leads`) e
  health check (`/health`)
- ✅ Alembic configurado (migrations)
- ✅ Celery configurado, com uma task (placeholder) por etapa do
  pipeline de enriquecimento
- ✅ Frontend conecta no backend, mostra status online/offline e uma
  tabela (vazia até o pipeline popular o banco)
- ✅ 34 testes automatizados passando (`pytest`)

## O que falta (próximas sessões)

Em ordem de prioridade combinada com o cliente:

1. ~~Fórmula da dívida ativa PGFN~~ ✅ confirmada com o Douglas em
   19/08/2026 e implementada (`pgfn_points`)
2. ~~Parser do CSV trimestral da PGFN~~ ✅ feito
3. Integração com a API do CNES
4. Busca inicial via Google Places (`search_google_places`)
5. Enriquecimento via Receita Federal (mesmo padrão do projeto SR)
6. Inferência de RQE via Firecrawl + IA (leitura de site/Instagram)
7. Validação de WhatsApp (Evolution API) e e-mail (Hunter.io + ZeroBounce)
8. `compute_lead_score` — juntar os pontos de todos os critérios (a
   PGFN já calcula os dela; falta fazer o mesmo pros outros 8 e somar)
9. Autenticação (JWT, admin/client) e telas do frontend (Kanban,
   exportação CSV/Excel pedida pela Sulamita)

**Nota operacional:** o download do CSV trimestral da PGFN em si (via
navegador, de dadosabertos.pgfn.gov.br) ainda é manual — colocar o
arquivo em `PGFN_DADOS_ABERTOS_DIR` (ver `.env`) e rodar
`refresh_pgfn_index`. Automação desse download fica para uma sessão
futura, se valer a pena frente ao volume (atualiza só 1x por trimestre).

---

## Rodando localmente

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha com suas chaves de API
uvicorn app.main:app --reload
```

Requer PostgreSQL e Redis rodando localmente (ou ajuste `DATABASE_URL`/
`REDIS_URL` no `.env`).

Rodar os testes:
```bash
pytest tests/ -v
```

### Worker (Celery)

```bash
cd backend
celery -A app.workers.celery_app worker --loglevel=info
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

---

## Modelo de Lead Score (referência rápida)

| Critério | Peso | Camada |
|---|---|---|
| Dívida ativa PGFN | 20 | Estruturado |
| Zona Franca / ALC | 20 | Estruturado |
| RQE / especialista | 15 | Inferência + CNES |
| WhatsApp ativo | 10 | Validação |
| E-mail validado | 10 | Validação |
| Presença digital forte | 10 | Inferência |
| Decisor identificável | 10 | Estruturado |
| CNPJ ativo (unidade) | 5 | Estruturado |
| Rating Google | 0 | Estruturado (zerado a pedido do cliente) |

Ver `backend/app/scoring/rules.py` para a definição completa, com fonte
e observações de cada critério.

---

**4Hands AI** — Caio Gabelini & Vinícius Cardoso
