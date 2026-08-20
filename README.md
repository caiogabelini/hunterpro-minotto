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
- ✅ **Integração com a API do CNES** (`app/services/cnes.py`) — endpoint
  confirmado por teste manual em 19/08/2026
  (`GET /cnes/estabelecimentos`, sem autenticação). A API não tem filtro
  por CNPJ nem retorna especialidade médica — o módulo busca todos os
  estabelecimentos de um município (paginando) e cruza o CNPJ do lead
  localmente. `enrich_cnes` confirma unidade registrada no CNES (reforço
  estrutural do sinal de RQE, não substitui a inferência via IA).
- ✅ **Enriquecimento via Receita Federal** (`app/services/receita_federal.py`)
  — via BrasilAPI (`GET /api/cnpj/v1/{cnpj}`, espelha os Dados Abertos do
  CNPJ, sem autenticação): CNAE, porte, situação cadastral, anos de
  mercado e decisor (identificado por palavra-chave na qualificação do
  sócio, já que não há tabela oficial confiável de códigos de
  qualificação disponível pra decorar). `enrich_receita_federal` também
  devolve `codigo_municipio_cnes`, fechando o gap que tinha ficado
  pendente da sessão do CNES.
- ✅ **Resolução de município → código IBGE** (`app/services/ibge_municipios.py`)
  — pra resolver esse gap, em vez de uma tabela estática decorada (o
  Brasil tem ~5.570 municípios; um dígito errado quebraria o cruzamento
  com o CNES silenciosamente), consulta a API oficial de Localidades do
  IBGE ao vivo. `codigo_ibge_para_cnes` confirma, por teste manual em
  19/08/2026, que o `codigo_municipio` do CNES é o código IBGE de 7
  dígitos sem o dígito verificador final.
- ✅ **Semente de leads a partir do arquivo bruto da Receita Federal**
  (`app/services/receita_federal_bulk.py`) — parser do arquivo
  ESTABELECIMENTOS dos Dados Abertos do CNPJ (mesmo formato do projeto
  SR Contábil, layout confirmado contra o PDF oficial da Receita em
  19/08/2026), filtrando por CNAE de consultório médico (grupo 8630,
  exceto a subclasse odontológica) e por UF/região, situação cadastral
  ativa por padrão. Task `gerar_semente_leads` lê os arquivos de
  `RECEITA_FEDERAL_DADOS_ABERTOS_DIR` e devolve a lista de CNPJs — esse
  arquivo NÃO tem o código IBGE do município (tem um código próprio da
  Receita, não cruzar com CNES/IBGE).
- ✅ **Junção com o arquivo EMPRESAS pra trazer razão social** — o
  ESTABELECIMENTOS só tem `nome_fantasia` (costuma vir vazio em empresas
  pequenas); `enriquecer_com_razao_social` cruza pela raiz do CNPJ
  (`cnpj_basico`, 8 primeiros dígitos) com o arquivo EMPRESAS
  (`K3241.K03200Y0.D10612.EMPRECSV` e afins, layout também confirmado
  contra o PDF oficial), buscando só os CNPJs básicos que já sobraram
  do filtro — não indexa os ~60 milhões de empresas do arquivo inteiro.
  Razão social é o campo principal do resultado de `gerar_semente_leads`
  (nome fantasia fica complementar); CNPJs sem correspondência no
  EMPRESAS ficam com razão social vazia, sem quebrar o pipeline.
- ✅ **`search_google_places` implementada de verdade** (`app/services/google_places.py`)
  — deixou de ser o ponto de entrada do pipeline; agora recebe os CNPJs
  já filtrados pela semente e busca cada um no Google Places (New) por
  Text Search, preferencialmente pela `razao_social`, caindo pro
  `nome_fantasia` só se ela vier vazia (`nome_busca_google_places`).
  Um só Text Search por estabelecimento — a API New já devolve
  site/rating/nº de avaliações direto ali, sem precisar de uma segunda
  chamada a Place Details. Contrato implementado contra a documentação
  oficial do Google em 20/08/2026, mas **ainda não testado contra a API
  real**: apesar de a `GOOGLE_API_KEY` já estar no `.env`, o sandbox
  desta sessão não permite chamada de rede autenticada (POST + header
  customizado) por nenhuma ferramenta disponível — ver ressalva
  detalhada em `app/services/google_places.py`. Wired em
  `process_lead_pipeline`, que agora também usa o `site` devolvido pelo
  Google Places como fonte automática de `site_url` quando não vier
  explícito.
- ✅ **Validação de WhatsApp** (`app/services/whatsapp.py`) — Evolution
  API (`POST {EVOLUTION_URL}/chat/whatsappNumbers/{EVOLUTION_INSTANCE}`,
  header `apikey`), endpoint e contrato já validados em produção no
  projeto SR Contábil. `formatar_numero_whatsapp` normaliza o telefone
  antes de chamar a API — decide se precisa prefixar o código do país
  (55) pelo TAMANHO do número (10/11 dígitos = nacional), não pelo
  prefixo, porque checar só "já começa com 55" é ambíguo (DDD 55 é do
  Rio Grande do Sul). Só bate na API se o resultado tiver 12-13 dígitos.
- ✅ **Enriquecimento de e-mail** (`app/services/email_enrichment.py`)
  — Hunter.io Email Finder (acha o e-mail do decisor) → validação de MX
  via dnspython → ZeroBounce (valida entregabilidade). Contrato das duas
  APIs também já validado em produção no SR Contábil. Só considera
  "aprovado" status `valid` ou `catch-all` do ZeroBounce; para cedo (sem
  gastar a consulta paga do ZeroBounce) se o Hunter.io não achar e-mail
  ou o domínio não tiver MX válido.
- ✅ **Varredura de site + inferência via IA** (`app/services/site_scraping.py`
  + `app/services/ai_enrichment.py`) — Firecrawl (`POST /v1/scrape`,
  `Authorization: Bearer`) extrai o markdown do site; endpoint e schema
  confirmados contra o `openapi.json` oficial do Firecrawl no GitHub. O
  link do Instagram é extraído por regex direto do markdown, sem IA,
  ignorando paths reservados (`p`, `reel`, `reels`, `stories`,
  `explore`, `accounts`, `share`). A inferência de RQE e
  serviços/especialidades usa Claude Haiku (`claude-haiku-4-5-20251001`)
  via chamada HTTP direta à Messages API (não o SDK — mantém o mesmo
  padrão de client `httpx` injetável/testável do resto do projeto),
  pedindo resposta em JSON; resposta malformada (a IA varia formato
  mesmo instruída) nunca lança exceção, só devolve valores vazios.
  **Nem Firecrawl nem Anthropic foram testados contra a API real ainda**
  — as duas chaves já estão no `.env` desde 20/08/2026, mas o sandbox
  desta sessão bloqueia `curl` via Bash e o `WebFetch` disponível só faz
  GET simples, sem headers customizados nem corpo POST; não dá pra
  autenticar contra nenhuma das duas API por essas ferramentas. O
  usuário confirmou que prefere não contornar essa limitação (ver nota
  operacional abaixo sobre a chave do Google que vazou sem querer numa
  tentativa). Também rechecado nesta sessão: o Firecrawl v2, promovido
  pela documentação renderizada do site como atual, **não tem spec
  verificável** no repositório oficial (só existe `v1-openapi.json` lá,
  e até o `openapi.json` genérico é v1.0.0) — decisão: continuar na v1,
  a única versão confirmável sem inventar schema.
- ✅ **`compute_lead_score`** (`app/scoring/compute_lead_score.py`) —
  junta o resultado de todas as etapas de enriquecimento nos 9 critérios
  de `rules.py` e devolve o score total (0-100) + o breakdown por
  critério (label, peso, pontos, camada de confiabilidade), pro futuro
  dossiê do lead mostrar de onde vieram os pontos. Fórmula da PGFN
  continua a única confirmada com o Douglas (cliente); as outras 8 são
  decisões de sessão, documentadas caso a caso no arquivo. Duas delas
  (`rqe_especialista`, cruzamento IA×CNES; `presenca_digital`, regra de
  site+Instagram) não tinham régua fechada nas reuniões com o cliente —
  **confirmadas pelo Caio Gabelini (responsável pelo produto) em
  19/08/2026** (confirmação de produto, não do Douglas).
- ✅ **`process_lead_pipeline`** (`app/workers/celery_app.py`) — a task
  orquestradora: dispara as etapas de enriquecimento pra um CNPJ na
  ordem que respeita as dependências reais entre elas (mapeadas
  explicitamente no arquivo — ex.: `enrich_cnes` só roda depois de
  `enrich_receita_federal` resolver `codigo_municipio_cnes`), junta os
  resultados, chama `compute_lead_score` e persiste tudo num `Lead`
  (upsert por CNPJ). Uma etapa que lança exceção não derruba as outras;
  uma etapa cuja etapa que a alimenta ainda não existe (Google Places),
  não tem chave configurada (Firecrawl) ou não tem dado disponível
  (`site_url`/`telefone`, que nenhuma etapa implementada produz ainda) é
  pulada com aviso no log, registrada em `etapas_puladas` no resultado —
  nunca quebra o pipeline inteiro.
- ✅ **Autenticação enxuta** (`app/models/user.py`, `app/core/security.py`,
  `app/api/routes/auth.py`, `app/api/deps.py`) — JWT (python-jose, HS256)
  com hash de senha via passlib/bcrypt. `POST /api/auth/login` (e-mail +
  senha → `access_token` com `user_id` e `role` no payload, 401 genérico
  pra credencial errada/e-mail inexistente/usuário inativo, sem vazar
  qual é o caso). `get_current_user` protege rota lendo o Bearer token;
  `require_admin` (peso morto por enquanto — nenhuma rota administrativa
  existe ainda) bloqueia quem não for `role == "admin"`. `GET /api/leads`
  já protegida — exige autenticação, qualquer papel. Sem endpoint de
  cadastro público de propósito: usuários são criados por admin via
  `scripts/create_user.py` (só a Minotto e a 4Hands usam o sistema por
  enquanto). **Bug real encontrado e corrigido**: `passlib==1.7.4` (já
  no requirements.txt) quebra com bcrypt>=4.0 — um `ValueError` enganoso
  ("password cannot be longer than 72 bytes") mesmo pra senha curta, não
  um erro claro de incompatibilidade. Fixado `bcrypt==3.2.2` em
  `requirements.txt`. Escopo deliberadamente enxuto — ver "O que falta"
  pro que ficou de fora conscientemente (refresh token, recuperação de
  senha, registro público).
- ✅ Frontend conecta no backend, mostra status online/offline e uma
  tabela (vazia até o pipeline popular o banco)
- ✅ 219 testes automatizados passando (`pytest`)

## O que falta (próximas sessões)

Em ordem de prioridade combinada com o cliente:

1. ~~Fórmula da dívida ativa PGFN~~ ✅ confirmada com o Douglas em
   19/08/2026 e implementada (`pgfn_points`)
2. ~~Parser do CSV trimestral da PGFN~~ ✅ feito
3. ~~Integração com a API do CNES~~ ✅ feito
4. ~~Enriquecimento via Receita Federal~~ ✅ feito (BrasilAPI), incluindo
   a resolução de município → código IBGE que fechou o gap do CNES
5. ~~Semente de leads (arquivo bruto de Estabelecimentos + filtro por
   CNAE/UF) + junção com EMPRESAS pra razão social~~ ✅ feito
6. ~~Inferência de RQE via Firecrawl + IA (leitura de site/Instagram)~~
   ✅ feito
7. ~~Validação de WhatsApp (Evolution API) e e-mail (Hunter.io + ZeroBounce)~~
   ✅ feito
8. ~~`compute_lead_score`~~ ✅ feito (`app/scoring/compute_lead_score.py`)
   — regras de `rqe_especialista` e `presenca_digital` confirmadas pelo
   Caio (produto) em 19/08/2026; se o Douglas pedir uma régua diferente
   pra qualquer uma das duas, ajustar as funções correspondentes
9. ~~Orquestração do pipeline (`process_lead_pipeline`) + persistência
   no `Lead`~~ ✅ feito
10. ~~`search_google_places` implementada de verdade~~ ✅ feito
    (`app/services/google_places.py`) — falta: (a) validar Google
    Places, Firecrawl e Anthropic contra as APIs reais — as três chaves
    já estão no `.env` desde 20/08/2026, mas o sandbox desta sessão não
    permite chamada de rede autenticada por nenhuma ferramenta
    disponível (ver ressalva em cada módulo); (b) implementar a task
    placeholder `enrich_linkedin`, que ainda não existe — quando
    existir, revisar a graduação de `presenca_digital_points` pra virar
    3 sinais em vez de 2, e adicionar a etapa em `process_lead_pipeline`;
    (c) rodar `gerar_semente_leads` e iterar sobre os CNPJs chamando
    `process_lead_pipeline` pra cada um — hoje isso é manual, não existe
    uma task que faça esse loop sozinha
11. ~~Autenticação (JWT, admin/client)~~ ✅ feito, escopo enxuto de
    propósito — deixado de fora nesta sessão, não esquecido:
    - **Refresh token**: hoje só existe `access_token` de vida curta
      (`ACCESS_TOKEN_EXPIRE_MINUTES`, 12h por padrão); expirado, o
      usuário precisa logar de novo. Sem refresh token nem revogação.
    - **Recuperação de senha**: não existe fluxo de "esqueci minha
      senha" — reset é manual, alguém com acesso ao banco roda
      `scripts/create_user.py` de novo ou atualiza `senha_hash` direto.
    - **Registro público**: de propósito não existe endpoint de
      cadastro — só a Minotto e a 4Hands usam o sistema, admin cria
      usuário via `scripts/create_user.py`. Se isso mudar (mais
      clientes, self-service), revisitar.
    - **Rotas administrativas**: `require_admin` existe e está testado,
      mas não protege nenhuma rota ainda — nenhuma rota admin-only
      foi criada nesta sessão.
    - Telas do frontend (Kanban, exportação CSV/Excel pedida pela
      Sulamita) — segue pendente, não é relacionado a auth.

**Nota operacional:** o download do CSV trimestral da PGFN em si (via
navegador, de dadosabertos.pgfn.gov.br) ainda é manual — colocar o
arquivo em `PGFN_DADOS_ABERTOS_DIR` (ver `.env`) e rodar
`refresh_pgfn_index`. Automação desse download fica para uma sessão
futura, se valer a pena frente ao volume (atualiza só 1x por trimestre).
Mesma lógica pros arquivos ESTABELECIMENTOS **e** EMPRESAS da Receita
Federal (arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/,
atualização mensal, publicados juntos no mesmo lote) — baixar manualmente
e colocar os dois em `RECEITA_FEDERAL_DADOS_ABERTOS_DIR` antes de rodar
`gerar_semente_leads`. Sem o(s) arquivo(s) EMPRESAS na pasta, a task
ainda funciona (filtra e devolve os CNPJs), só que sem razão social.

**Nota operacional (19/08/2026):** `FIRECRAWL_API_KEY` foi adicionada em
`app/core/config.py` mas **não** em `backend/.env.example` — o sandbox
desta sessão bloqueou leitura/escrita em qualquer arquivo `.env*`
(inclusive `.example`). Adicionar manualmente uma linha
`FIRECRAWL_API_KEY=` no `.env.example` (e preencher no `.env` local).

**Nota operacional (20/08/2026):** com `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`
e `FIRECRAWL_API_KEY` preenchidas no `.env`, a intenção era validar
Google Places, Firecrawl e Anthropic contra as APIs reais (mesmo padrão
já usado pro CNES/IBGE/BrasilAPI). Não foi possível: o sandbox desta
sessão bloqueia `curl` via Bash (qualquer chamada de rede, testado até
contra `google.com` puro) e o `WebFetch` disponível só faz GET simples,
sem headers customizados nem corpo POST — nenhuma das três APIs aceita
autenticação assim (todas exigem `POST` + header customizado). Numa
tentativa de contornar isso pro Google Places (cuja API legada aceita a
chave como query string, testável via GET), a chave acabou vazando na
saída visível da sessão por engano. O usuário foi avisado na hora e
optou por **não** validar ao vivo nesta sessão — as três integrações
ficam implementadas contra documentação oficial, não contra chamada
real. Se isso for revisitado numa sessão futura com outro ambiente/
ferramental, considerar rotacionar a `GOOGLE_API_KEY` antes.

**Nota operacional (20/08/2026):** `alembic/versions/` estava **vazio**
antes desta sessão — não existe migration baseline pra tabela `leads`
(só a nova, `6102ef9fc509_cria_tabela_users.py`, escrita à mão porque
não há Postgres acessível com as credenciais do `.env` neste sandbox
pra rodar `--autogenerate`). Isso quer dizer que `alembic upgrade head`
num banco totalmente vazio cria só `users`, **não** `leads` — a tabela
`leads` precisa continuar existindo por outro meio (como já estava
sendo feito antes) até alguém gerar a migration baseline dela também.
Não é escopo desta sessão consertar, só ficou documentado pra não
surpreender quem for rodar as migrations em produção.

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

Aplicar as migrations e criar o primeiro usuário:
```bash
alembic upgrade head
python scripts/create_user.py --email seu@email.com --senha "uma-senha-forte" --role admin
```
(sem endpoint de cadastro público de propósito — ver "O que falta")

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
