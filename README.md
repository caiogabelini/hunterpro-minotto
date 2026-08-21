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
    api.ts                cliente HTTP pro backend (leads, lista paginada, mensagens, login,
                          PATCH status, dashboard, admin/buscas)
    tokens.css             paleta de cor (navy/dourado, tema claro) + sombra/transição/espaçamento (design tokens)
    index.css               tipografia + estilos globais + badges compartilhados (consome tokens.css)
    context/AuthContext.tsx  token JWT em memória (React Context) + login/logout + `role` decodificado (admin/client)
    components/           Sidebar, AuthenticatedLayout, ProtectedRoute, AdminRoute (guard
                          admin-only), KanbanColumn, KanbanCard, LossReasonModal,
                          PriorityBadge, Modal (genérico, reutilizável), SimuladorReceitaModal,
                          FechamentoModal
    servicosFechamento.ts   lista dos 4 serviços fixos + tradução chave -> label,
                          compartilhado entre FechamentoModal e LeadDossierPage
    pages/                LoginPage, KanbanPage, ListaLeadsPage, LeadDossierPage,
                          DashboardPage, BuscaLeadsPage (admin only)
    App.tsx                React Router: /login (público, sem sidebar) e / + /leads +
                          /leads/:id + /dashboard + /busca-leads (admin only, guard
                          AdminRoute) -- todas protegidas, com sidebar via AuthenticatedLayout
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
- ✅ **Kanban de leads no `Lead`** (`app/models/lead.py::KanbanStatus`) —
  campo `kanban_status` (string, default `"novo_lead"`, 9 valores fixos
  de funil) e `motivo_perda` (opcional, só relevante quando
  `kanban_status == "perdido"`), migration
  `a3f7c1d92b4e_adiciona_kanban_status_e_motivo_perda.py` (encadeada
  depois de `6102ef9fc509`/users, escrita à mão como as duas anteriores —
  **validada contra Postgres 16 real** num cluster temporário e
  descartável, mesmo padrão das duas migrations anteriores: upgrade
  encadeado (`leads` → `users` → esta), `downgrade -1` reverte só esta,
  re-upgrade limpo, schema final de `kanban_status`/`motivo_perda`
  conferido exato contra `app/models/lead.py`; ver "Migrations" no
  CLAUDE.md pro relato completo). `PATCH
  /api/leads/{lead_id}/status` (protegida, qualquer papel) muda a coluna
  do lead; valida o valor de `kanban_status` contra o enum e exige
  `motivo_perda` quando o destino é `"perdido"` (422 se faltar). `GET
  /api/leads` ganhou filtro opcional `kanban_status`. 9 testes novos em
  `tests/test_leads_routes.py`.
- ✅ **Primeira tela do frontend: Login + Kanban** — React Router
  (`/login` público, `/` protegida). Token JWT guardado **só em memória**
  (`React Context`, `context/AuthContext.tsx`), nunca em
  `localStorage`/`sessionStorage`/cookie — recarregar a página desloga de
  propósito, evita o risco de um script malicioso (XSS) ler o token de um
  storage persistente. `ProtectedRoute` redireciona pro `/login`
  automaticamente sem token válido. Kanban com as 9 colunas fixas do
  funil (Novo Lead → ... → Ganho/Perdido, ordem em
  `src/kanbanStatuses.ts`, espelhando o enum do backend), cada card com
  razão social, badge de prioridade (A/B/C colorido) e score. Arrastar
  pra "Perdido" abre um modal pedindo `motivo_perda` antes de confirmar a
  chamada ao PATCH — nas outras colunas o PATCH dispara direto
  (otimista, com rollback se a API falhar/401). Biblioteca de
  drag-and-drop: **`@dnd-kit/core`** (ver justificativa abaixo). Paleta e
  tipografia da identidade visual do material de apresentação do cliente
  (navy `#292C5D`, fundo `#0B0D22`, dourado `#F5C842`, Bebas Neue +
  Inter via Google Fonts) centralizadas em `src/index.css`. `npm run
  build` e `npm run lint` passam limpos; **não testado num navegador de
  verdade** — este sandbox não tem acesso de rede/browser pra exercitar
  o fluxo de login→drag-and-drop manualmente (mesma limitação de rede já
  registrada nas notas de Google Places/Firecrawl/Anthropic), só
  validado via `tsc`/build + revisão de código.
  **Escolha do `@dnd-kit/core`** (não `react-beautiful-dnd`, `react-dnd`
  nem HTML5 drag-and-drop nativo): `react-beautiful-dnd` está
  arquivado/sem manutenção desde 2023 (Atlassian descontinuou); `react-dnd`
  é mais antigo e mais verboso pra um Kanban simples de colunas fixas
  (precisa configurar backend de HTML5 manualmente, API mais baixo
  nível); drag-and-drop nativo do HTML5 tem suporte mobile/touch fraco,
  ruim pra uma ferramenta comercial que pode ser usada em tablet. O
  `@dnd-kit` é ativamente mantido, sem dependência de `react-dom`
  legado, tem suporte a touch/teclado de acessibilidade nativo, e a API
  (`useDraggable`/`useDroppable`/`DndContext`) é pequena o bastante pra
  um board de 9 colunas sem precisar de virtualização ou reordenação
  dentro da mesma coluna (não pedido nesta tela).
- ✅ **Segunda tela do frontend: Dossiê do lead** (`/leads/:id`,
  protegida, mesmo padrão do Kanban) — cada card do Kanban agora é
  clicável (`onClick` no `KanbanCard`, convive sem conflito com o
  drag-and-drop do `@dnd-kit` porque o `PointerSensor` só ativa o drag
  depois de um deslocamento mínimo de 6px; um clique sem arrastar dispara
  o `onClick` normalmente) e navega pro dossiê. Backend ganhou `GET
  /api/leads/{lead_id}` (protegida, mesmo padrão do resto) e `LeadRead`
  foi expandido com os campos que `list_leads` não precisava (CNAE, anos
  de mercado, Simples Nacional, situação cadastral, telefone, site,
  Instagram, LinkedIn empresa/decisor, rating/avaliações do Google,
  serviços) — 3 testes novos em `tests/test_leads_routes.py`. A tela
  mostra: cabeçalho (razão social, nome fantasia, CNPJ, badge de
  prioridade, score total em destaque); seção separada e destacada pra
  dívida ativa PGFN (verde "nenhuma pendência" ou vermelho com o número
  de inscrições — nunca em branco); dados cadastrais; contato (com
  indicador visual pra WhatsApp ativo/e-mail validado); presença digital
  (site/Instagram/LinkedIn como links, rating Google, serviços); e o
  breakdown do score — os 9 critérios de `SCORING_CRITERIA`, cada um com
  pontos/peso máximo (ex. "RQE / especialista: 10/15 pts") e uma
  cor/etiqueta de camada de confiabilidade (estruturado/inferência/
  validação, mesmo vocabulário de `app/scoring/rules.py::SignalLayer`,
  serializado em `Lead.score_detalhes.breakdown` por `compute_lead_score`
  — ver `app/scoring/compute_lead_score.py`). Botão de voltar pro Kanban.
  Mesma paleta/tipografia da tela anterior; o badge de prioridade virou
  estilo compartilhado (`src/index.css`) já que agora é usado em duas
  telas. `npm run build`/`npm run lint` limpos; mesma ressalva de sempre
  — não testado num navegador de verdade (sandbox sem acesso de
  rede/browser).
- ✅ **Bugfix: breakdown do score não aparecia no dossiê** — reportado
  como "Score ainda não calculado" mesmo com `score_detalhes` preenchido
  no banco. Investigação ponta a ponta (schema → endpoint → frontend)
  não achou nada quebrado no caminho canônico (confirmado com dois
  round-trips reais, um deles um `uvicorn` de verdade servindo HTTP,
  não só `TestClient`) — a fragilidade real é que `fetchLead`/`fetchLeads`
  confiam cegamente no shape de `res.json()` (tipado `any` pelo DOM lib,
  sem garantia nenhuma em runtime). Corrigido extraindo a leitura do
  breakdown pra uma função pura e defensiva,
  `getScoreBreakdown` (`frontend/src/leadScore.ts`), que trata a entrada
  como `unknown`, aceita o formato canônico e um array cru como
  fallback, e nunca lança. `npm run test` (vitest, adicionado nesta
  sessão) tem 4 casos novos em `leadScore.test.ts` — confirmado que o
  primeiro caso falha contra uma implementação propositalmente quebrada
  antes de restaurar a correção, então é uma trava de regressão real.
  Reforçado também `tests/test_leads_routes.py` no backend: um teste
  novo roda o motor de score de verdade (`montar_score_lead`, os 9
  critérios) em vez de um breakdown de 1 item hand-crafted. Ver
  CLAUDE.md "Lead dossier score breakdown bug" pro relato completo da
  investigação.
- ✅ **Dossiê enriquecido — campos que já existiam no sistema mas não
  apareciam na tela** — três colunas novas no `Lead`
  (`app/models/lead.py`): `mensagem_abordagem` (Text, na época só o
  espaço reservado — a geração via IA ficou pra uma sessão futura, ver
  bullet abaixo pra quando isso foi implementado), `divida_ativa_valor_total` (Float) e
  `divida_ativa_ajuizado` (Boolean) — `app/services/pgfn.py::PgfnDebtSummary`
  já calculava os dois últimos (`valor_total`, `algum_ajuizado`) desde a
  sessão do parser da PGFN, mas o `Lead` nunca persistia, só a contagem
  (`divida_ativa_qtd_inscricoes`). Migration
  `fbd6142f0632_adiciona_mensagem_abordagem_e_campos_...py` (encadeada
  depois de `a3f7c1d92b4e`/kanban) — **validada contra Postgres 16
  real**, mesmo processo das três migrations anteriores (cluster
  temporário, upgrade encadeado/downgrade -1/re-upgrade/schema
  conferido, cluster apagado ao final). `LeadRead` ganhou os três campos
  novos + `rqe_fonte`, `cnes_codigo` e `updated_at`, que já existiam no
  modelo desde sessões anteriores mas nunca tinham sido expostos pelo
  schema — 2 testes novos/expandidos em `tests/test_leads_routes.py`.
  No dossiê (`LeadDossierPage.tsx`): a seção de PGFN agora mostra o
  valor total formatado em R$ e um selo vermelho destacado
  ("⚠️ Execução judicial em andamento") quando `divida_ativa_ajuizado`;
  nova seção "RQE / Especialidade" com cor diferente pra confirmação via
  CNES (alta confiança, verde) vs. menção só no site (inferência,
  dourado); um badge do status atual do Kanban no cabeçalho (com o
  `motivo_perda` junto quando for "Perdido"); nova seção "Abordagem
  sugerida" mostrando `mensagem_abordagem` ou "Mensagem ainda não
  gerada" (nesta sessão, só o espaço de exibição — o botão "Gerar
  mensagem com IA" veio na sessão seguinte, ver bullet abaixo); WhatsApp
  virou link `wa.me/{numero}` (formatação do número em `src/format.ts`,
  **espelha deliberadamente** a mesma heurística de
  `app/services/whatsapp.py::formatar_numero_whatsapp` — prefixa o
  código do país pelo TAMANHO do número, não pelo prefixo, pelo mesmo
  motivo do DDD 55/Rio Grande do Sul documentado lá); e-mail ganhou
  botão de copiar com feedback "Copiado!" (`navigator.clipboard`); e
  `created_at`/`updated_at` formatados ("Encontrado em DD/MM/AAAA" ·
  "Atualizado há X"). 12 testes novos no frontend (`vitest`):
  `format.test.ts` espelha os mesmos casos de
  `backend/tests/test_whatsapp.py` pra `formatarNumeroWhatsapp` (mesmo
  risco de um projeto anterior — reimplementar uma heurística sem
  reimplementar os testes que a validam é como bugs sutis passam
  despercebidos). `npm run build`/`npm run lint`/`npm run test` limpos.
- ✅ **Geração da mensagem de abordagem via IA** — completa o que tinha
  ficado como só o espaço reservado na sessão anterior.
  `app/services/ai_enrichment.py::gerar_mensagem_abordagem` (mesmo
  modelo `claude-haiku-4-5-20251001`, mesmo client `httpx` injetável de
  `inferir_dados_site`) monta um prompt com os sinais reais do lead
  presentes (razão social, decisor, dívida ativa PGFN, RQE, rating do
  Google, situação cadastral — nunca inventa um dado ausente) e pede uma
  mensagem curta (2-3 frases) de e-mail de primeiro contato, com uma
  regra de tom explícita e não-negociável no prompt: dívida ativa PGFN
  tem que ser mencionada de forma consultiva ("oportunidade de revisão
  financeira"), nunca acusatória ("sua empresa está endividada").
  **Diferente de `inferir_dados_site`, essa função nunca lança** — erro
  de rede/HTTP e resposta vazia/sem bloco de texto viram `""` em vez de
  propagar exceção; é o endpoint novo, `POST
  /api/leads/{lead_id}/gerar-abordagem` (protegido, mesma proteção do
  resto das rotas de lead), que decide o que fazer com uma string vazia
  — não persiste, devolve 502 com uma mensagem clara pro frontend
  mostrar. Em caso de sucesso, persiste em `Lead.mensagem_abordagem` e
  devolve o lead atualizado. 10 testes novos (5 em
  `test_ai_enrichment.py` cobrindo o prompt/tratamento de erro da
  função, 5 em `test_leads_routes.py` cobrindo o endpoint — geração bem-
  sucedida, persistência confirmada no banco, lead inexistente 404,
  resposta vazia da IA vira 502 sem sobrescrever uma mensagem anterior,
  sem token retorna 401).

  No dossiê (`LeadDossierPage.tsx`, só a seção "Abordagem sugerida"
  mexida): quando `mensagem_abordagem` é `null`, o estado vazio agora
  vem acompanhado de um botão "Gerar mensagem com IA"; ao clicar, mostra
  "Gerando..." (botão desabilitado enquanto isso) e substitui o estado
  vazio pela mensagem ao terminar; quando já existe mensagem, um botão
  menor "Gerar novamente" aparece embaixo dela pra regenerar. Erro da
  chamada (rede, 502 da IA) mostra uma frase amigável abaixo do botão,
  sem quebrar o resto da tela — nunca deixa a seção num estado
  quebrado/sem saída.

  **Nota sobre validação, diferente de todo o resto deste módulo**: as
  outras integrações via Anthropic/Firecrawl/Google Places neste projeto
  ficaram marcadas como "não testadas contra a API real" porque o
  *sandbox de desenvolvimento* não permite chamada de rede autenticada
  (ver "Network access from Bash is blocked" no CLAUDE.md) — mas essa
  ressalva é sobre o ambiente onde o código foi *escrito*, não sobre o
  ambiente onde ele *roda*. Uma vez em produção, o endpoint de geração é
  chamado pelo navegador do usuário final contra o backend real, que tem
  a `ANTHROPIC_API_KEY` de produção configurada — o botão "Gerar
  mensagem com IA" na tela do dossiê **é a validação real**, não fica
  pendente pra uma sessão futura com outro ambiente/ferramental como as
  demais (essa nota continua valendo integralmente — ver bullet abaixo
  pra onde esse endpoint mora agora).

  **⚠️ Superseded pela sessão seguinte** (mesmo dia — ver bullet
  abaixo): `POST /api/leads/{lead_id}/gerar-abordagem` (sem canal) e a
  persistência em `Lead.mensagem_abordagem` descritos acima **não
  existem mais** — substituídos por um endpoint por canal e uma tabela
  de histórico (`LeadMessage`). `Lead.mensagem_abordagem` continua na
  tabela (deprecated, dado antigo preservado), mas a rota foi removida
  de propósito, não apenas esquecida.
- ✅ **Abordagem multi-canal (e-mail + WhatsApp, com histórico)** —
  substitui a mensagem genérica única do bullet anterior por uma
  mensagem própria por canal, cada geração virando um registro novo
  (nunca sobrescreve). Modelo novo, `LeadMessage`
  (`app/models/lead_message.py`): `id`, `lead_id` (FK pra `leads.id`,
  indexado), `canal` (`"email"` \| `"whatsapp"`, enum `CanalMensagem`),
  `conteudo`, `assunto` (só preenchido no canal e-mail), `gerado_em`.
  Migration `c47a9e2b1d05_cria_tabela_lead_messages.py` (encadeada
  depois de `fbd6142f0632`) — **validada contra Postgres 16 real**,
  mesmo processo de sempre (cluster temporário, upgrade encadeado de
  todas as cinco migrations/downgrade -1/re-upgrade/schema conferido,
  cluster apagado ao final).

  `app/services/ai_enrichment.py::gerar_mensagem_abordagem` ganhou um
  parâmetro `canal` obrigatório que muda tom/tamanho/formato do prompt:
  **e-mail** — mais formal, corpo com 3-4 frases, IA responde em JSON
  (`{"assunto": ..., "corpo": ...}`, parseado com o mesmo `_extrair_json`
  tolerante a formato já usado por `inferir_dados_site`); **WhatsApp** —
  mais direto/casual, 1-2 frases, resposta em texto puro, sem assunto. A
  mesma regra de tom consultivo pra dívida ativa PGFN (nunca acusatória)
  vale pros dois canais. `canal` fora de `"email"`/`"whatsapp"` levanta
  `ValueError` (erro de programação — a rota já valida antes de chamar).

  Dois endpoints novos, substituindo o antigo:
  - `POST /api/leads/{lead_id}/gerar-abordagem/{canal}` — gera e cria
    um `LeadMessage` novo (nunca sobrescreve; 422 canal inválido, 404
    lead inexistente, 502 se a IA não gerar nada, sem criar registro
    vazio).
  - `GET /api/leads/{lead_id}/mensagens` — devolve só a mensagem mais
    recente de cada canal (o histórico completo fica no banco, não
    exposto por essa rota); 404 se o lead não existir.

  Testes por canal reescritos/expandidos em `test_ai_enrichment.py` (tom
  e formato distintos por canal, JSON malformado no canal e-mail, canal
  inválido) e em `test_leads_routes.py` (geração por canal, histórico
  não sobrescrito ao regenerar, listagem só com a mais recente por
  canal, 404 nos dois endpoints novos) — suite total subiu de 243 pra
  253 testes.

  No dossiê (`LeadDossierPage.tsx`, só a seção "Abordagem sugerida"
  mexida): a seção agora mostra um card por canal — "E-mail" só aparece
  se `lead.email` existir, "WhatsApp" só se `lead.whatsapp_ativo` for
  `true` (nunca os dois ausentes ao mesmo tempo sem uma mensagem clara
  de "nenhum canal disponível"; Instagram de propósito fora, como
  pedido). Cada card tem seu próprio "Gerar mensagem com IA"/"Gerar
  novamente", mesmo padrão de loading/erro já existente. Card de e-mail,
  depois de gerado: botão "Abrir no e-mail"
  (`mailto:{email}?subject=...&body=...`, `encodeURIComponent` cuidando
  de acentos/quebra de linha/caracteres especiais). Card de WhatsApp,
  depois de gerado: botão "Enviar mensagem gerada"
  (`wa.me/{numero}?text=...`, mesma codificação, mesmo `formatarNumeroWhatsapp`
  já usado na ação rápida do topo do dossiê) — distinto do link "Abrir
  WhatsApp" que já existia na seção de Contato (aquele abre uma conversa
  vazia, esse pré-preenche com a mensagem gerada). `npm run
  build`/`npm run lint`/`npm run test` limpos.
- ✅ **Polimento visual "premium SaaS" das 3 telas** (Login, Kanban,
  Dossiê) — sessão só de frontend, nenhum arquivo de backend tocado.
  **⚠️ Superseded em 21/08/2026** — o aviso abaixo dizia que o tema
  continuava escuro por decisão permanente; uma sessão seguinte fez
  exatamente essa troca, a pedido explícito do cliente (ver bullet
  "Retema completo do frontend" mais abaixo, tema atual é claro). Tema
  continuava escuro (navy `#292C5D`/fundo `#0B0D22`/dourado `#F5C842`)
  — decisão validada com o cliente desde a primeira tela, aquela sessão
  reforçou profundidade (sombra, gradiente) dentro do mesmo tema, sem
  trocar pra tema claro. Aviso equivalente em `frontend/src/tokens.css`
  também foi atualizado na sessão do retema.
  - **Sidebar fixa** (`components/Sidebar.tsx` + `AuthenticatedLayout.tsx`,
    novos) — presente em Kanban e Dossiê (não em Login). Logo "HunterPro"
    no topo, item "Kanban" (destacado quando ativo), "Busca de Leads" e
    "Dashboard" como itens desabilitados com selo "em breve" (essas
    telas não existem — nenhuma rota falsa foi criada, só o espaço
    reservado visualmente). Rodapé: papel do usuário logado (decodificado
    do JWT — hoje o token só tem `user_id`/`role`, sem e-mail/nome; ver
    comentário em `Sidebar.tsx` pra esse limite) + botão Sair, que saiu
    do topo da página do Kanban e foi pra cá. "Fixa" é `position: sticky`
    dentro de um layout flex (`AuthenticatedLayout.css`), não `position:
    fixed` — mesmo resultado visual, sem precisar sincronizar manualmente
    a largura da sidebar com uma margem no conteúdo.
  - **`frontend/src/tokens.css`** (novo, importado antes de `index.css`
    em `main.tsx`) — sombras sutis (`--shadow-sm/md/lg`, mais uma
    `--shadow-gold-hover` pra hover de botão/card com brilho de marca),
    transições (`--transition-fast` 150ms/`--transition-smooth` 250ms),
    escala de espaçamento (`--space-1` a `--space-12`, múltiplos de
    4px), e `--bg-gradient` (gradiente radial sutil, aplicado no `body`
    em `index.css`, substituindo o fundo chapado anterior).
  - **`lucide-react`** instalado — os 3 emojis/setas de texto que
    existiam (⚠️ na seção de PGFN, ← no botão voltar, ↗ no link do
    WhatsApp) viraram ícones vetoriais (`AlertTriangle`, `ArrowLeft`,
    `ExternalLink`). Cada seção do Dossiê ganhou um ícone no cabeçalho
    (`Building2`, `Phone`, `Globe`, `Stethoscope`, `Sparkles`, `Gauge`,
    e `Landmark`/`ShieldCheck` alternando na PGFN conforme tem ou não
    pendência) via um componente compartilhado, `SectionHeading`.
  - **Badges mais "pill"** — prioridade (A/B/C), status do Kanban, e as
    camadas do score (estruturado/inferência/validação, que antes eram
    só texto cinza e agora são pills coloridas por camada) — raio bem
    arredondado (`border-radius: 999px`), mais contraste de cor. Badge
    de prioridade ganhou uma variante maior (`priority-badge-lg`) pro
    cabeçalho do Dossiê.
  - **Botões**: hover consistente (leve elevação + brilho dourado via
    `--shadow-gold-hover` nos primários, borda/cor dourada nos
    secundários), `:active`/`:disabled` bem definidos, transição em
    todos usando os tokens.
  - **Login**: gradiente de fundo (herdado do `body`, nenhuma mudança
    local necessária), mais respiro (padding maior, `max-width` do card
    subiu de 360px pra 400px), ícone (`Building2`) e subtítulo
    acrescentados pra reforçar hierarquia visual, sombra `--shadow-lg`
    no card.
  - **Kanban**: colunas vazias mostram "Nenhum lead aqui ainda" (antes
    ficava um espaço em branco); cards ganharam sombra + hover (leve
    elevação, `translateY(-2px)`) sinalizando que são interativos, e
    uma animação sutil de entrada (`kanban-card-entrar`, fade + leve
    translate) toda vez que um card é montado — inclusive ao mudar de
    coluna, já que mover pra outra coluna troca o pai do elemento no
    DOM e React remonta o card ali (uma animação de posição de verdade,
    tipo FLIP, precisaria de uma lib como framer-motion — fora de
    escopo desta sessão).
  - **Dossiê** (a tela mais trabalhada): ícone em cada `<h2>` de seção;
    hierarquia tipográfica mais forte — `dt`/`dd` dos campos passaram a
    ter tamanhos/pesos diferentes (label pequena/maiúscula/cinza, valor
    maior/em negrito), e um divisor sutil (`border-bottom` fraco) separa
    cada linha de campo dentro de um card, exceto a última; score total
    saltou de 40px pra 60px com um leve `text-shadow` dourado, badge de
    prioridade ganhou a variante `-lg`; loading da geração de mensagem
    por IA trocou o texto "Gerando..." por um ícone `Loader2` girando
    (`@keyframes spin` em `tokens.css`, classe `.spin` reutilizável).
  - **Sem validação visual real** — este sandbox não tem navegador (ver
    "Network access..." no CLAUDE.md, mesma limitação de sempre, agora
    também relevante pra CSS/layout, não só chamadas de API). Todo
    valor de cor/espaçamento/sombra foi escolhido com cuidado e
    documentado, mas não foi visualmente conferido numa tela de
    verdade — validação real fica por conta do usuário. `npm run
    build`/`npm run lint`/`npm run test` confirmam que nada quebrou
    tecnicamente (TypeScript, lint, os 12 testes de `vitest` já
    existentes), não que o resultado visual está correto.
- ✅ **Bugfix: campos de login com fundo branco do navegador** —
  exatamente o tipo de problema que a ressalva acima ("sem validação
  visual real") previa; reportado pelo usuário ao olhar a tela de
  verdade. Os `<input>` de e-mail/senha usavam um fundo translúcido
  (`rgba(0,0,0,0.25)`) que o autofill do Chrome simplesmente ignora —
  ele pinta um fundo branco/amarelo próprio em campo autofilled, não
  respeitando `background`/`color` do CSS. Corrigido com uma cor sólida
  nova, `--bg-elevated` (`index.css`), e o override específico de
  autofill em `LoginPage.css` (`input:-webkit-autofill` com
  `box-shadow` inset gigante + `-webkit-text-fill-color`, mais
  `input:autofill` pro Firefox). `color-scheme: dark` também adicionado
  globalmente. Escopo só na tela de Login, único lugar com `<input>`
  (o campo do modal de "motivo da perda" é um `<textarea>`, não afetado
  por esse comportamento de autofill). Ver CLAUDE.md "Visual polish
  pass" pro detalhe completo do porquê de cada peça da correção.
- ✅ **Dossiê reestruturado em abas** — inspirado numa referência visual
  (Radar Comercial) compartilhada pelo cliente, mas usando só dado que
  já existia no dossiê (nenhum campo novo, nenhuma chamada de IA nova).
  Cabeçalho fixo (razão social, linha "município/UF · nicho" — nicho é
  texto fixo "Clínica Médica", já que todo lead deste projeto já é
  filtrado pra esse nicho desde a semente — e uma linha de resumo
  "Score: X · Classificação: [A/B/C mapeado pra Excelente/Bom/Regular]
  · Prioridade: [badge] · Canal: [WhatsApp ou E-mail, o que estiver
  confirmado]") + 4 abas em pills: **Dados** (os mesmos 3 cards +
  RQE de antes, só reagrupados, com a dívida ativa PGFN no topo);
  **Contatos** (novo — o decisor como card de pessoa com WhatsApp/
  E-mail/LinkedIn em chips clicáveis, estruturado como lista pra
  comportar mais contatos no futuro); **Análise IA** (o breakdown do
  score de sempre, mais uma lista nova "Sinais positivos" — derivada
  automaticamente dos critérios do breakdown que bateram o peso máximo,
  não é IA nova, é reorganização do mesmo dado); **Mensagens** (a seção
  de Abordagem Sugerida de sempre, geração/regeneração/mailto/wa.me
  sem nenhuma mudança de lógica). O número de score "hero" com brilho
  dourado que ficava no cabeçalho tinha se mudado pra dentro da aba
  Análise IA nesta sessão (achando que duplicava a linha de resumo) —
  **revertido no mesmo dia, ver bullet abaixo**, feedback visual real
  pediu de volta. Painel lateral de "Insights da IA" **não** foi
  adicionado nesta sessão, de propósito — fica pra uma sessão futura
  dedicada, já alinhado com o cliente. `npm run build`/`npm run lint`/
  `npm run test` limpos; sem validação visual real, mesma ressalva de
  sempre.
- ✅ **Dois ajustes no Dossiê a partir de feedback visual real** — a
  primeira vez nesta sequência de sessões que um retorno olhando a tela
  de verdade corrigiu uma decisão tomada só "no escuro" (sandbox sem
  navegador). (1) O card isolado "RQE / Especialidade" — que sobrava
  sozinho numa última linha da aba Dados (3 cards cabem certinho no
  grid, o 4º ficava torto) — foi removido; RQE virou um badge
  condicional na linha de resumo do cabeçalho fixo ("RQE: Confirmado via
  CNES", verde, ou "RQE: Inferido (site)", dourado — mesmas cores do
  card antigo), omitido inteiramente quando `rqe_confirmado` é
  null/false, já que é sinal estratégico de ICP e precisa estar sempre
  visível, não escondido numa aba. (2) O número de score "hero" (badge
  de prioridade grande + número gigante com brilho dourado + label)
  voltou pro canto superior direito do cabeçalho, de onde tinha saído
  na sessão da reestruturação em abas — a aba Análise IA não repete
  mais esse bloco. A linha de resumo perdeu o item de texto "Score: X"
  (julgamento pedido explicitamente pelo usuário, pra não duplicar o
  número grande do lado). Ver CLAUDE.md "Dossier tabbed restructure"
  (seção de acréscimo no final) pro relato completo, inclusive um aviso
  pra sessões futuras não repetirem esse vaivém. `npm run build`/`npm
  run lint`/`npm run test` limpos.
- ✅ **5ª aba "Insights" no Dossiê — análise estratégica gerada por IA
  sob demanda** (20/08/2026, sessão seguinte à reestruturação em abas —
  fecha a pendência do item 15 abaixo). Backend: `Lead.insights_ia`
  (JSON — `resumo_estrategico`, `potencial_oportunidade`,
  `recomendacao_abordagem`, `estrategia_comunicacao`, `cta_sugerido`) +
  `Lead.insights_gerado_em` (migration validada contra Postgres real,
  mesmo processo de sempre); `gerar_insights_estrategicos` em
  `app/services/ai_enrichment.py` (Claude Haiku, mesmo padrão defensivo
  de `gerar_mensagem_abordagem` — nunca lança, `{}` em erro/resposta
  malformada), com um contexto de prompt bem mais completo que o da
  mensagem de abordagem (`_montar_contexto_lead_insights` — inclui
  score total, breakdown por critério, presença digital, anos de
  mercado, Zona Franca, não só o subconjunto usado numa mensagem de
  e-mail/WhatsApp curta); `POST /api/leads/{lead_id}/gerar-insights`
  (protegida, 404/502 nos mesmos moldes de `gerar-abordagem`, sem
  histórico — cada geração sobrescreve a anterior, diferente de
  `LeadMessage`). Frontend: mesmo padrão de UX "Gerar"/"Gerando..."/
  "Gerar novamente" já validado em Abordagem Sugerida; badge de
  potencial (verde/dourado/cinza — "médio" reaproveita a cor dourada da
  paleta em vez de introduzir amarelo novo), lista de recomendação com
  ícone, CTA sugerido num card com fundo dourado sólido (único bloco da
  tela com esse tratamento, de propósito, pra ler como ação a tomar —
  ⚠️ desde o retema claro abaixo, esse fundo é navy, não mais dourado;
  a ideia de "único bloco com fundo cheio" continua igual, só a cor
  mudou), e "Gerado há X" via `formatRelative`. `getInsights` (`src/insights.ts`)
  faz o parsing defensivo de `insights_ia` vindo da rede, mesmo
  raciocínio de `getScoreBreakdown` (bug de score já documentado abaixo)
  — nunca deixa um shape inesperado quebrar a tela. `npm run build`/
  `npm run lint`/`npm run test` e `pytest` limpos; sem validação visual
  real, mesma ressalva de sempre.
- ✅ **Retema completo do frontend: escuro (navy/dourado) → claro**
  (21/08/2026, sessão seguinte à aba Insights), ancorado na marca real
  da Minotto Contabilidade — logo navy com detalhe dourado discreto,
  visual institucional em fundo branco/cinza claro. Todas as telas
  (Login, Sidebar, Kanban, Dossiê completo — 5 abas) recoloridas; navy
  (`--color-primary`) virou a cor dominante de botões/links/destaques,
  dourado (`--color-gold`) passou a aparecer só em detalhes pequenos
  (badges, sublinhado do score, borda de destaque do card CTA) — não
  mais em botões primários, aba/nav ativa, ou títulos de seção, que
  eram os usos "dominantes" de dourado no tema anterior. Cores
  semânticas (verde/dourado/vermelho/azul de prioridade e camada do
  score) mantiveram a mesma lógica, mas os tons pastel do tema escuro
  foram recalibrados — eram ilegíveis como texto em fundo branco (verde
  antigo, por ex., tinha só 1.49:1 de contraste contra branco; mínimo
  do WCAG AA é 4.5:1) — todo par cor/fundo novo foi conferido com
  contraste calculado, não só escolhido de olho. Brilho dourado do
  número de score (`text-shadow`) virou um sublinhado dourado fino, já
  que glow não funciona em fundo claro. Truque de autofill do Chrome no
  Login foi recolorido pro novo fundo claro. `--bg-gradient` (gradiente
  radial escuro de fundo) foi removido, não recalibrado — fundo agora é
  chapado, como um site institucional de verdade. Toda variável antiga
  (`--navy`, `--gold-dim`, `--gray`, `--bg`, `--bg-elevated`,
  `--shadow-gold-hover`) foi renomeada, não mantida como alias — nenhum
  arquivo CSS do frontend ficou com cor hardcoded fora de variável (
  auditado antes e depois da sessão). Ver CLAUDE.md "Complete retheme
  to light mode" pro relato completo, inclusive as decisões de
  julgamento (qual uso de dourado virou navy vs. qual continuou
  dourado, cor do card CTA). `npm run build`/`npm run lint`/`npm run
  test` limpos (18 testes, sem mudança — sessão só de CSS, nenhuma
  lógica de componente tocada); sem validação visual real, mesma
  ressalva de sempre — reforçada aqui porque um retema inteiro é
  particularmente arriscado de validar sem navegador.
- ✅ **Dashboard (Fase 1)** (21/08/2026) — nova rota `/dashboard`,
  4ª tela do frontend, item "Dashboard" da sidebar deixou de ser "em
  breve". Backend: `GET /api/dashboard/summary` (leads no mês vs. limite
  do plano fixo — `settings.PLANO_LEADS_MES = 50` —, score médio, leads
  em negociação, taxa de conversão, todos calculados via `SELECT`
  agregado, sem N+1), `GET /api/dashboard/acoes-recomendadas` (4
  categorias fixas de "próxima ação sugerida", cada uma sua própria
  contagem, categorias com contagem zero omitidas), `GET`/`PUT
  /api/dashboard/premissas` (as 3 premissas do Simulador de Receita,
  persistidas por usuário em `User.dashboard_premissas`, JSON novo —
  migration validada contra Postgres real, mesmo processo de sempre;
  `GET` sem premissas salvas devolve um padrão calculado a partir da
  base de leads real, não persiste sozinho). Frontend: cards de KPI
  (score médio/leads em negociação/taxa de conversão), barra de
  consumo do plano (preenchimento navy, vira laranja — `--color-alert`,
  token novo — a partir de 90% do limite), lista de ações recomendadas
  com link "Ver leads" pro Kanban (**ainda sem filtro real aplicado,
  simplificação desta fase** — ver item 16 em "O que falta"), simulador
  de receita com cálculo em tempo real (`leads_qualificados × taxa_fechamento%
  × ticket_medio`) e botão "Salvar premissas". 17 testes novos no
  backend cobrindo cada métrica, as 4 categorias de ação (inclusive
  omissão de zero e isolamento entre elas), persistência de premissas
  por usuário, e taxa de conversão sem quebrar com zero leads. Nenhum
  teste novo no frontend — o único cálculo novo (a multiplicação da
  receita estimada) é simples demais pra justificar o mesmo tratamento
  de função pura + teste que `getScoreBreakdown`/`getInsights` tiveram.
  `npm run build`/`npm run lint`/`npm run test` e `pytest` limpos; sem
  validação visual real, mesma ressalva de sempre.
- ✅ **Dashboard (Fase 2)** (21/08/2026) — completa o dashboard com
  funil de conversão e motivos de perda, mesma tela `/dashboard` da
  Fase 1 (sem migration nova — as duas seções só agregam colunas que já
  existiam). Backend: `GET /api/dashboard/funil` (as 8 etapas do
  Kanban exceto "perdido", sempre todas as 8 mesmo com contagem zero —
  zero é informação, não ruído — cada uma com `percentual` sobre o
  total de leads não-perdidos; **⚠️ é uma fotografia da distribuição
  atual, não uma taxa de conversão histórica entre etapas** — este
  projeto não tem tabela de histórico de transição de status, só o
  `kanban_status` atual de cada lead, então não dá pra calcular "de
  quem passou por X, quantos avançaram pra Y" — ver limitação
  documentada no código, na tela e no item 17 de "O que falta"),
  `GET /api/dashboard/motivos-perda` (ranking dos motivos de perda
  agrupados por texto EXATO — sem normalização por IA, de propósito —,
  ordenado por contagem decrescente, top 10). Frontend: funil como
  barras horizontais (etapa "Ganho" destacada em verde, judgment call
  não pedido explicitamente mas consistente com o resto do vocabulário
  de cor do app) com a mesma ressalva de honestidade metodológica
  reproduzida na própria tela, não só no código; motivos de perda como
  ranking numerado com barra relativa ao motivo mais frequente da
  própria lista, estado vazio com tom positivo ("Nenhum lead perdido
  registrado ainda — ótimo sinal!", ícone `PartyPopper` verde). 10
  testes novos no backend (todas as 8 etapas presentes mesmo zeradas,
  percentual calculado certo inclusive sem dividir por zero, "perdido"
  nunca aparece no funil, agrupamento por texto exato não funde motivos
  parecidos, lista vazia, limite de 10 respeitado). Nenhum teste novo
  no frontend — mesmo raciocínio da Fase 1 (cálculo de largura de barra
  é simples demais pra função pura + teste dedicados). `npm run
  build`/`npm run lint`/`npm run test` e `pytest` limpos; sem validação
  visual real, mesma ressalva de sempre.
- ✅ **Simulador de Receita movido pra card + modal** (21/08/2026) —
  frontend-only, `PUT`/`GET /api/dashboard/premissas` da Fase 1 sem
  mudança nenhuma. O simulador deixou de ser uma seção própria no fim
  da página e virou um 4º card na seção "Resumo executivo" (fundo em
  tom suave de dourado, o único dos 4 cards com esse destaque visual),
  mostrando o valor calculado + as premissas atuais como texto pequeno
  + um botão "Simular cenário" que abre um modal com os 3 campos
  editáveis. **Primeiro modal reutilizável do projeto**
  (`components/Modal.tsx`, novo — overlay escurecido, fecha ao clicar
  fora/X/Esc) — o modal antigo do Kanban (`LossReasonModal.tsx`) é uma
  implementação própria mais antiga e não foi tocado/migrado nesta
  sessão (fora do escopo pedido), os dois convivem lado a lado com
  classes CSS deliberadamente diferentes (`.hp-modal-*` vs. `.modal-*`)
  pra não colidir no bundle único do Vite. Decisão de design que vale
  entender antes de mexer: o modal (`SimuladorReceitaModal.tsx`) guarda
  um RASCUNHO PRÓPRIO das premissas, separado do valor já salvo que o
  card mostra — o pedido foi "cálculo atualiza em tempo real [dentro do
  modal]" mas "AO SALVAR... atualiza o card do topo", então editar (ou
  fechar sem salvar) nunca muda o que o card já exibe; só um `PUT` bem-
  sucedido atualiza os dois. Nenhum teste novo (nem backend, que não
  mudou, nem frontend — a fórmula da receita é a mesma de sempre, só
  mudou de lugar). `npm run build`/`npm run lint`/`npm run test`
  limpos; sem validação visual real, mesma ressalva de sempre —
  particularmente a lógica de rascunho separado, fácil de acertar no
  código e errar na prática.
- ✅ **Funil de conversão passa a incluir "perdido"** (21/08/2026,
  mesmo dia do item acima, sessão seguinte) — ajuste em `GET
  /api/dashboard/funil` a pedido do cliente: "perdido" virou a 9ª etapa
  da lista (antes eram só 8, "perdido" ficava de fora por completo) e o
  `percentual` de TODAS as etapas passou a ser calculado sobre o total
  de leads incluindo perdidos, não mais só sobre os "não perdidos". Duas
  consequências diretas: a soma dos 9 percentuais agora fecha em ~100%
  (antes fechava ~100% de um subconjunto, o que ficava enganoso ao lado
  do resto do dashboard), e o total agora bate exatamente com a
  contagem real de leads no Kanban e com o denominador que
  `taxa_conversao` (no `summary`) já usava — os dois endpoints
  concordam sobre o que é "o total" agora, antes não. **A limitação de
  fotografia-não-taxa-de-conversão-histórica continua valendo
  integralmente** — só o que entra no total mudou, não a limitação em
  si (ver item 17 em "O que falta", atualizado com uma nota apontando
  pra este ajuste). Frontend: a barra de "Perdido" ganhou cor própria
  (`--color-danger`, vermelho — mesmo tom já usado na seção "Motivos de
  perda" logo abaixo, por consistência) pra ficar visualmente distinta
  de "Ganho" (verde) e das etapas de progresso (navy), deixando claro
  que é uma saída do funil, não mais uma etapa. Teste do funil
  reescrito: 9 etapas (não mais 8), "perdido" por último com o label
  certo, percentual recalculado com "perdido" no denominador, soma dos
  percentuais fecha em ~100% (com tolerância só pro arredondamento por
  etapa), e a contagem total bate com uma query direta no banco. `npm
  run build`/`npm run lint`/`npm run test` e `pytest` limpos; sem
  validação visual real, mesma ressalva de sempre.
- ✅ **Painel admin de "busca mensal"** (22/08/2026) — dispara o
  pipeline completo (`gerar_semente_leads` + `process_lead_pipeline`
  por CNPJ) manualmente pela tela, visível e acessível SÓ pra usuários
  admin (Caio/Vinícius) — nunca aparece na sidebar nem carrega por URL
  direta pra um usuário "client" (Douglas/Sulamita). **⚠️ Dispara custo
  real de API em produção, não é simulação** — aviso permanente na tela
  + modal de confirmação antes de disparar, pedido explícito desta
  sessão. Backend: modelo `BuscaLeads` (histórico de execuções, uma
  linha por disparo, nunca sobrescrita — migration validada contra
  Postgres real), task orquestradora `executar_busca_mensal` (Celery) —
  roda `gerar_semente_leads` uma vez, depois `process_lead_pipeline`
  pra cada CNPJ, erro isolado por CNPJ nunca derruba o loop inteiro
  (acumula em `erros`, uma string por CNPJ que falhou), status vira
  `"erro"` só se mais de 50% dos CNPJs falharam (critério simples,
  configurável só via constante no código). `POST /api/admin/buscas`
  (cria o registro e dispara `.delay()` — **primeira chamada
  assíncrona de verdade a uma task Celery neste projeto**, todo o resto
  sempre chamou tasks-irmãs direto, em processo, pra testabilidade),
  `GET /api/admin/buscas` (histórico) e `GET /api/admin/buscas/{id}`
  (status pra polling) — todas atrás de `require_admin` (403 pra
  "client", não 401 — **primeira rota deste projeto a realmente usar
  `require_admin`**, que já existia desde a sessão de autenticação mas
  nunca tinha uma rota pra proteger). Frontend: `role` (decodificado do
  JWT) centralizado em `AuthContext` (antes vivia só dentro da
  Sidebar) — reaproveitado pelo item condicional da sidebar E pelo novo
  guard `AdminRoute`, que redireciona um "client" que digitar a URL
  direto pro Kanban, não pro login (ele já está autenticado, só não tem
  permissão pra essa tela — mesma distinção 401 vs. 403 do backend).
  Tela: botão "Executar busca do mês" → modal de confirmação → spinner
  "Buscando leads..." **sem barra de progresso falsa** (pedido
  explícito de honestidade — não há progresso percentual granular pra
  mostrar) com polling a cada 4s até a busca terminar (retoma sozinho
  se a página for recarregada no meio de uma busca em andamento),
  resumo final (CNPJs encontrados/leads processados/erros, com lista
  expansível de erros) e histórico de buscas anteriores. 18 testes
  novos no backend (7 da task, cobrindo status ao longo da execução,
  isolamento de erro por CNPJ, critério de >50% de falha, semente
  vazia, falha da própria `gerar_semente_leads`; 11 das rotas, incluindo
  403 explícito pra "client" em todas as 3). Nenhum teste novo no
  frontend (lógica de estado/efeitos, não função pura). `npm run
  build`/`npm run lint`/`npm run test` e `pytest` limpos; sem validação
  visual real, mesma ressalva de sempre — particularmente relevante
  aqui pela quantidade de lógica de estado (polling, retomada,
  confirmação) que só uma tela de verdade confirma de ponta a ponta.
- ✅ **Trava contra disparo duplo em `POST /api/admin/buscas`**
  (22/08/2026) — antes de criar uma nova `BuscaLeads`, a rota checa se
  já existe alguma com `status="executando"`; se sim, **409 Conflict**
  com mensagem pronta pro usuário ("Já existe uma busca em andamento,
  iniciada em DD/MM/AAAA HH:MM."), sem criar registro novo nem disparar
  a task. ⚠️ Best-effort, não uma garantia formal sob concorrência real
  (`SELECT` simples, não um lock de banco) — documentado assim de
  propósito, ver item 21 em "O que falta". Frontend: `ConflictError`
  novo em `api.ts` (mesmo padrão de `UnauthorizedError`, carrega a
  mensagem do backend pronta), tratado separado de erro genérico —
  mensagem em tom informativo (navy, não vermelho — não é uma falha do
  usuário) e a tela passa a acompanhar a busca já em andamento
  (reaproveitando o polling que já existia), o que também é o que faz o
  botão "Executar busca do mês" sumir sozinho enquanto uma busca já
  está rodando. 3 testes novos no backend (409 sem criar registro, e
  duas variações confirmando que uma busca nova pode ser criada depois
  que a anterior concluiu com sucesso OU com erro). `npm run
  build`/`npm run lint`/`npm run test` e `pytest` limpos.
- ✅ **Tela "Lista de Leads"** (22/08/2026) — nova rota `/leads`, item
  próprio na sidebar (entre Kanban e Dashboard, ícone `List` — não
  `Search`, já usado por "Busca de Leads", pra não confundir as duas
  telas), visível pra qualquer papel. Backend: `GET /api/leads/lista`
  **endpoint próprio e aditivo** — `GET /api/leads` (usado pelo Kanban)
  continua exatamente como estava, sem paginação nem busca por texto;
  a suíte antiga inteira (`test_leads_routes.py`, 34 casos) roda
  intacta pra provar isso, mais um teste novo específico confirmando
  que `GET /api/leads` continua devolvendo uma lista simples, não o
  envelope `{items, total, pagina, por_pagina}` do endpoint novo. Busca
  por `razao_social`/`nome_fantasia`/`cnpj` (case-insensitive via
  `ILIKE`), filtros de prioridade/etapa, ordenação por
  score/data-de-criação nos dois sentidos (validada contra lista
  fechada, 422 fora dela), paginação com total real sobre os filtros
  aplicados. **⚠️ Achado durante os testes**: o `lower()` do SQLite
  (usado nos testes) só normaliza ASCII — `"CLÍNICA".lower()` no SQLite
  não vira `"clínica"` de verdade — então o teste de case-insensitivity
  usa só texto sem acento por esse motivo (não é limitação do endpoint,
  é do SQLite de teste; Postgres em produção faz o folding certo).
  Frontend: tabela com Nome (razão social + nome fantasia menor),
  CNPJ, Contato (só ícones de WhatsApp/e-mail confirmados — nunca o
  texto do telefone/e-mail em si), Decisor, Município/UF, Score (com
  badge de prioridade), Etapa (badge reaproveitando as cores já usadas
  no dossiê) e Encontrado em; busca com debounce de 400ms, filtros de
  prioridade/etapa, cabeçalhos de Score/Encontrado em clicáveis pra
  ordenar, paginação na base; clicar numa linha navega pro dossiê já
  existente (`/leads/:id`). 17 testes novos no backend (busca parcial
  por nome/nome fantasia/CNPJ, ordenação nos dois sentidos e nas duas
  colunas, paginação com total e páginas disjuntas, filtros combinados,
  ambos os 422 de validação, e a regressão do endpoint antigo). Nenhum
  teste novo no frontend (lógica de tabela/filtro, não função pura).
  `npm run build`/`npm run lint`/`npm run test` (1 aviso aceito de
  `set-state-in-effect` no efeito de busca — padrão padrão de
  data-fetching que precisa re-rodar a cada filtro, mesma categoria do
  aviso já aceito em `AuthContext.tsx`) e `pytest` limpos; sem validação
  visual real, mesma ressalva de sempre.
- ✅ **Dados de fechamento ao mover lead pra "Ganho"** (23/08/2026) —
  espelha o padrão já existente de "Perdido"/`motivo_perda`, do lado
  positivo. Backend: `Lead` ganhou `servicos_vendidos` (JSON, lista de
  strings — sem enum fechado no banco, os 4 valores fixos + texto livre
  de "Outro" cabem igual, validação de opções é do frontend),
  `tipo_contrato` ("pontual" | "recorrente", validado contra esse
  conjunto fechado) e `valor_fechamento` (R$, > 0) — migration validada
  contra Postgres real. `PATCH /{lead_id}/status` passa a exigir os
  três quando o novo `kanban_status` é "ganho" (422 com mensagem
  específica por campo faltando/inválido, mesmo formato do erro já
  existente pra `motivo_perda`). **Decisão deliberada, diferente do
  padrão de `motivo_perda`**: os dados de fechamento NÃO são apagados
  se o lead for movido pra outra etapa depois — `motivo_perda` é
  limpo ao sair de "perdido" porque descreve por que algo não
  aconteceu (deixa de fazer sentido se não é mais perdido);
  serviços/contrato/valor descrevem uma venda que JÁ aconteceu, então
  continuam válidos como registro histórico mesmo que o lead seja
  reclassificado depois. Frontend: `FechamentoModal.tsx` (novo,
  construído sobre o `Modal` genérico — segundo componente a usar ele,
  depois do `SimuladorReceitaModal`) abre ao arrastar um card pra
  "Ganho" no Kanban, com checkboxes pros 4 serviços fixos + "Outro"
  (campo de texto livre revelado ao marcar), seletor de tipo de
  contrato, campo de valor com label dinâmico ("Valor único"/"Valor
  mensal"), e botão "Confirmar" desabilitado até tudo estar preenchido
  (mesma validação do backend, replicada no cliente). No dossiê, uma
  nova seção "Fechamento" (aba Dados, só quando `kanban_status ==
  "ganho"` e os 3 campos existem) mostra os dados de forma simples —
  serviços traduzidos de volta pra label legível, tipo de contrato,
  valor formatado — com um leve tom verde marcando o card como um
  desfecho positivo. 12 testes novos no backend (cada campo
  faltando/inválido individualmente, lista vazia vs. ausente, o caminho
  feliz com múltiplos serviços, texto livre de "Outro" sobrevivendo na
  lista, confirmação de que os dados NÃO são apagados ao sair de
  "ganho", e que mover pra uma etapa intermediária continua sem exigir
  nada). Nenhum teste novo no frontend. `npm run build`/`npm run
  lint`/`npm run test` (mesmos 2 avisos já aceitos, nenhum novo) e
  `pytest` limpos; sem validação visual real, mesma ressalva de sempre.
- ✅ 347 testes automatizados passando (`pytest`) + 18 testes no
  frontend (`vitest`)

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
12. ~~Primeira tela do frontend (Login + Kanban)~~ ✅ feito
    (20/08/2026) — ver acima.
13. ~~Segunda tela do frontend (Dossiê do lead, `/leads/:id`)~~ ✅ feito
    (20/08/2026) — ver acima. Deliberadamente fora de escopo **desta**
    sessão, não esquecido (telas seguintes, uma por sessão, validando
    cada uma antes da próxima):
    - **Exportação CSV/Excel** (pedida pela Sulamita) — nenhuma tela
      nem endpoint de export existe ainda.
    - **Edição de dados do lead pelo dossiê** — a tela de dossiê é
      só leitura; não há formulário nem endpoint `PUT`/`PATCH` pra
      editar os dados cadastrais/contato/presença digital de um lead
      (só `kanban_status`/`motivo_perda`, via o Kanban).
    - **Histórico de mudanças de status** — o dossiê mostra o
      `kanban_status` atual e o `motivo_perda` (quando houver), mas não
      existe um log de quando/quem moveu o lead entre colunas ao longo
      do tempo; precisaria de uma tabela nova (ex. `lead_status_history`)
      pra registrar isso, que não existe ainda.
    - **Cadastro de usuário pelo frontend** — continua só via
      `scripts/create_user.py` (ver item 11 acima); nenhuma tela de
      admin foi criada.
14. ~~Polimento visual "premium SaaS" (sidebar, tokens de design,
    ícones, sombras/hover) nas 3 telas existentes~~ ✅ feito (20/08/2026)
    — ver acima. Pendências deixadas de propósito:
    - **Validação visual real** — este sandbox não tem navegador; todo
      o polimento foi validado só tecnicamente (`build`/`lint`/`test`),
      não visualmente. Primeira coisa a conferir numa sessão com acesso
      a browser antes de confiar 100% no resultado.
    - **Nome/e-mail real do usuário na sidebar** — hoje mostra só o
      papel ("Administrador"/"Cliente"), decodificado do JWT, porque o
      payload do token não carrega nome nem e-mail (ver
      `app/api/routes/auth.py`). Exigiria adicionar esses campos ao
      payload (ou um endpoint `GET /api/auth/me`) — não fica pendente
      por complexidade, só não foi pedido nesta sessão de polimento
      visual puro.
    - **Animação de posição real ao mover card entre colunas do
      Kanban** — o que existe hoje é uma animação de entrada (fade +
      leve translate) no card remontado na coluna nova, não uma
      transição de posição de verdade (tipo FLIP). Precisaria de uma
      lib como `framer-motion` — avaliar se vale a pena numa sessão
      futura, não foi adicionada agora pra não crescer o escopo de
      dependências além do pedido (`lucide-react` foi a única nova).
    - **"Busca de Leads" e "Dashboard"** continuam só itens reservados
      na sidebar ("em breve", desabilitados) — nenhuma dessas telas
      existe, de propósito (não inventar rota falsa). ⚠️ ~~"Dashboard"~~
      ✅ deixou de ser reservado numa sessão seguinte (21/08/2026, ver
      item 16 abaixo) — rota `/dashboard` real, item da sidebar ativo.
      "Busca de Leads" continua reservado, sem mudança.
15. ~~Dossiê reestruturado em abas (Dados/Contatos/Análise IA/Mensagens)~~
    ✅ feito (20/08/2026) — ver acima. ~~Painel "Insights da IA"~~ ✅
    também feito, numa sessão seguinte no mesmo dia (5ª aba "Insights",
    ver bullet dedicado abaixo) — não mais pendência. Pendência restante:
    - **Validação visual real** — mesma ressalva do item 14: o layout
      das abas em si e os chips da aba Contatos foram validados só
      tecnicamente, nunca vistos numa tela de verdade. (O reposicionamento
      do número de score pra dentro da aba Análise IA especificamente
      **já foi corrigido no mesmo dia** a partir de feedback visual real
      — ver bullet "Dois ajustes no Dossiê a partir de feedback visual
      real" acima; o número voltou pro cabeçalho.)
16. ~~Dashboard (Fase 1) — resumo executivo, consumo do plano, ações
    recomendadas, simulador de receita~~ ✅ feito (21/08/2026) — ver
    bullet dedicado abaixo. Pendência deixada de propósito:
    - **Link "Ver leads" das Ações Recomendadas não filtra o Kanban de
      verdade ainda** — hoje navega pra `/` (Kanban) sem aplicar nenhum
      filtro, mesmo a API já devolvendo `kanban_status_filtro`/
      `filtro_chave` prontos pra isso. Simplificação aceita
      explicitamente pra esta fase — fica pra uma sessão futura que
      também decida como representar um filtro "leads com dívida PGFN"
      no Kanban, que não é redutível a um único `kanban_status`.
    - **Validação visual real** — mesma ressalva de toda tela nova
      construída neste sandbox sem navegador; particularmente relevante
      aqui pela barra de progresso e pelo card de CTA "em destaque" das
      ações recomendadas, nunca vistos renderizados de verdade.
17. ~~Dashboard (Fase 2) — funil de conversão, motivos de perda~~ ✅
    feito (21/08/2026) — ver bullet dedicado abaixo. Pendência deixada
    de propósito:
    - **⚠️ Limitação conhecida do funil, não um bug a corrigir sem
      pensar**: `GET /api/dashboard/funil` mostra a distribuição ATUAL
      de leads por etapa do Kanban (uma fotografia), não uma taxa de
      conversão real entre etapas — este projeto não guarda histórico
      de quando cada lead mudou de status, só o status de agora.
      Calcular "de quem passou pela etapa X, quantos avançaram pra Y"
      exigiria uma tabela de histórico de transição que não existe.
      Documentado no código (`get_funil`), na tela (nota abaixo do
      funil) e aqui — não adicionar uma métrica de "taxa de conversão
      entre etapas" numa sessão futura sem primeiro construir esse
      histórico. Esta limitação continua valendo integralmente mesmo
      depois do ajuste do item 19 abaixo (que só mudou o que entra no
      total, não a limitação em si).
      **⚠️ Descrição original desatualizada em um ponto** — quando
      escrito, o funil excluía "perdido" tanto da lista quanto do
      denominador do percentual; isso mudou no item 19 abaixo (21/08/2026,
      mesmo dia, sessão seguinte) — "perdido" agora é a 9ª etapa da
      lista e entra no denominador. O resto desta descrição (fotografia,
      não taxa de conversão real) continua exato.
    - **Motivos de perda agrupados por texto exato, sem normalização**
      — "Preço alto" e "Achou caro" contam como dois motivos distintos
      hoje, de propósito (pedido explícito: "agrupamento simples, sem
      IA/normalização"). Uma normalização semântica via IA (mesmo
      padrão já usado em `gerar_insights_estrategicos`) é um upgrade
      natural pra uma sessão futura, se o cliente achar o ranking
      fragmentado demais na prática.
    - **Validação visual real** — mesma ressalva de sempre; peculiar
      aqui porque as duas seções são só barras horizontais posicionadas
      via `style` inline (largura em `%`), nunca vistas renderizadas.
18. ~~Simulador de Receita movido pra card + modal~~ ✅ feito
    (21/08/2026) — ver bullet dedicado abaixo. Pendência deixada de
    propósito:
    - **`LossReasonModal` (Kanban) não foi migrado pro novo componente
      `Modal` genérico** — os dois modais convivem lado a lado hoje,
      cada um com seu próprio estilo (`.modal-*` em `KanbanPage.css`
      pro antigo, `.hp-modal-*` em `Modal.css` pro novo). Consolidar os
      dois no mesmo componente é um upgrade natural pra uma sessão
      futura, não feito aqui por estar fora do escopo pedido.
    - **Validação visual real** — mesma ressalva de sempre; a lógica de
      rascunho separado do modal (o card só atualiza depois de salvar,
      editar e fechar sem salvar não deve mudar nada) é o tipo de coisa
      fácil de acertar no código e errar na prática — primeira coisa a
      conferir na tela de verdade.
19. ~~Funil de conversão passa a incluir "perdido"~~ ✅ feito
    (21/08/2026, mesma data do item 17, sessão seguinte) — ver bullet
    dedicado abaixo. Nenhuma pendência nova deixada por este ajuste —
    a limitação de fotografia-não-taxa-de-conversão do item 17 continua
    valendo exatamente como estava.
20. ~~Painel admin de "busca mensal" (disparo manual do pipeline
    completo, só admin)~~ ✅ feito (22/08/2026) — ver bullet dedicado
    abaixo. Pendências deixadas de propósito:
    - **`gerar_semente_leads()` roda sem filtro nenhum** — a task
      `executar_busca_mensal` chama `gerar_semente_leads()` sem
      parâmetros (usa os defaults de UF/região dela, que é "sem
      filtro" — todo o Brasil). Deixar o admin escolher UF/região no
      próprio painel antes de disparar é um upgrade natural, não pedido
      nesta sessão.
    - ~~Nada impede duas buscas rodando ao mesmo tempo~~ ✅ trava simples
      adicionada numa sessão seguinte (22/08/2026, ver item 21 abaixo) —
      `POST /buscas` agora devolve 409 se já existe uma `"executando"`.
      **Não é uma garantia de concorrência real** (não usa `SELECT ...
      FOR UPDATE` nem constraint de banco) — ver ressalva completa no
      item 21 e no docstring de `disparar_busca`.
    - **Sem cancelamento** — uma vez disparada, uma busca corre até o
      fim (sucesso ou erro por CNPJ), não há como interrompê-la pelo
      painel nem por outro endpoint.
    - **Validação visual real** — mesma ressalva de sempre; esta tela
      em particular tem bastante lógica de estado (polling, retomada
      após reload, modal de confirmação) que só um teste manual na tela
      de verdade confirma de ponta a ponta.
21. ~~Trava simples contra disparo duplo em `POST /api/admin/buscas`~~
    ✅ feito (22/08/2026) — ver bullet dedicado abaixo. Pendência
    deixada de propósito:
    - **Ainda não é uma garantia de concorrência real** — é um `SELECT`
      seguido de `INSERT`, não um lock de banco nem uma constraint
      única. Dois requests chegando no mesmíssimo instante ainda podem,
      em teoria, criar duas buscas `"executando"`. Suficiente pro caso
      real (clique duplo, dois admins na mesma tela em segundos
      normais de reação humana) — endurecer isso de verdade (constraint
      de banco) só se algum dia isso se provar um problema na prática.
22. ~~Tela "Lista de Leads" (busca/filtros/ordenação/paginação)~~ ✅
    feito (22/08/2026) — ver bullet dedicado abaixo. Pendências
    deixadas de propósito:
    - **`por_pagina` fixo em 25 no frontend, sem seletor de tamanho de
      página na tela** — o backend já aceita `por_pagina` até 100 via
      query param, só a UI não expõe um controle pra mudar isso ainda.
    - **Filtro de "Etapa" não inclui um jeito rápido de excluir
      "Perdido"** (tipo "todas as etapas ativas") — hoje é "todas as 9"
      ou uma de cada vez, igual ao filtro de status que `GET /api/leads`
      já tinha.
    - **Sem exportação (CSV/Excel)** — item antigo de "O que falta" (ver
      itens 12/13 mais atrás), continua de fora, agora relevante de novo
      já que esta é a primeira tela com uma listagem tabular de
      verdade.
23. ~~Dados de fechamento ao mover lead pra "Ganho"~~ ✅ feito
    (23/08/2026) — ver bullet dedicado abaixo. Pendências deixadas de
    propósito:
    - **Exibição no dossiê é só o dado bruto** (lista de serviços, tipo
      de contrato, valor formatado) — pedido explícito desta sessão;
      uma exibição mais rica/analítica (ex.: alguma visualização
      agregada de receita fechada por serviço no Dashboard) fica pra
      uma sessão futura.
    - **`LossReasonModal` continua sem migrar pro `Modal` genérico** —
      mesma pendência já registrada no item 18; `FechamentoModal` é o
      segundo componente (depois de `SimuladorReceitaModal`) a usar o
      `Modal` novo, o modal de "Perdido" continua com a implementação
      ad-hoc mais antiga.

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

**Nota operacional (20/08/2026, atualizada no mesmo dia):**
`alembic/versions/` estava **vazio** antes desta sessão — não existia
migration baseline pra tabela `leads`. **Corrigido**: agora tem duas
migrations encadeadas, `e119018824e8_cria_tabela_leads.py` (baseline de
`leads`, sem `down_revision` — é a raiz) → `6102ef9fc509_cria_tabela_users.py`
(depois dela). As duas foram escritas à mão (não por
`--autogenerate` — o Postgres do `.env` deste projeto continua
inacessível neste sandbox, autenticação falha mesmo com a URL real) e
**validadas contra um Postgres 16 de verdade**: um cluster temporário e
descartável foi provisionado só pra esse teste (`initdb`/`pg_ctl`, porta
separada, apagado ao final — sem tocar no Postgres já rodando no
ambiente, que é de outro serviço). `alembic upgrade head` aplicou as
duas sem erro, `alembic downgrade base` reverteu sem erro, e o schema
final bateu exato com os modelos (`\d leads`, `\d users`) — tipos,
nullability, os dois índices únicos. O que **não** foi validado é
contra a `DATABASE_URL` real deste projeto especificamente — só contra
Postgres 16 em geral, já que essa credencial segue inacessível.

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

Rodar os testes (vitest — só funções puras por enquanto, ver CLAUDE.md):
```bash
npm run test
```

Pra logar, é preciso ter um usuário criado no backend primeiro (ver
`python scripts/create_user.py` acima) — não há cadastro público pelo
frontend, de propósito.

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
