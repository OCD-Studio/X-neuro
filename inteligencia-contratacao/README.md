# Inteligência de Contratação Pública (PT)

Sistema que ingere os contratos públicos portugueses, calcula **indicadores de risco** explicáveis e
(em fases seguintes) mapeia **relações registadas** entre entidades e pessoas, para investigação humana.

> **Princípio:** isto não identifica "corruptos". Produz um score de risco + os sinais que o justificam +
> as fontes. Um ajuste direto ou um concurso com um só concorrente podem ser perfeitamente legais.
> Ver a página **Metodologia e limites** na app (`/metodologia`).

## Estado

| Fase | Estado |
|---|---|
| 0 — Reconhecimento | ✅ (ver "Achados da Fase 0") |
| 1 — Skeleton: 1 ano, BD, 1 indicador, teste, lista básica | ✅ 2025 ingerido; indicador *concorrente único* |
| 1b — Calibração do score (antecipada a pedido) | ✅ taxas de referência + score 0–100 por entidade |
| 2 — Ingestão completa, scheduler diário, backfill, qualidade | ✅ 2012–2026; sincronização diária; página `/qualidade` |
| 3 — Todos os indicadores + lista completa | ✅ 12 indicadores testados; lista < 1 s; minimização de pessoas singulares |
| 4 — Resolução de entidades + grafo temporal + perfis de entidade | ⏳ |
| 5 — Perfis de pessoa + organigrama | ⏳ |
| 6 — Alertas + polish | ⏳ |

## Arranque rápido

### Alojamento partilhado (cPanel / Krystal)

Ver [`docs/INSTALAR_KRYSTAL.md`](docs/INSTALAR_KRYSTAL.md) — `passenger_wsgi.py`, `.env`, Cron Job diário e
`python -m contratacao.cli verificar` para confirmar os requisitos (PostgreSQL ≥ 12, permissões, rede, disco).

### Produção (Docker)

```bash
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > .env
docker compose up -d          # db + web (porta 8000, só localhost) + agendador
```

O agendador corre a sincronização ao arrancar e todos os dias às 06:17 (Europe/Lisbon). Pôr um reverse proxy com
HTTPS (Caddy/nginx) à frente da porta 8000. Requisitos: ~9 GB de disco para a BD com 2012–2026; ~200 MB de RAM
(ingestão e pontuação em streaming).

### Desenvolvimento

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user@localhost/contratacao   # PostgreSQL >= 14
python -m contratacao.cli sincronizar --max-backfill 20       # tudo de uma vez (~30 min)
uvicorn contratacao.api:app --port 8000                       # http://localhost:8000
```

Outros comandos: `migrar`, `ingerir --ano 2025 [--ficheiro x.zip]`, `pontuar`.
Sem processo residente: cron do sistema, p.ex. `17 6 * * * cd /app && python -m contratacao.cli sincronizar`.

Testes: `pytest` (os testes de integração correm se `TEST_DATABASE_URL` apontar para uma BD **descartável** —
apagam os schemas).

## Arquitetura (atual)

```
dados.gov.pt  API: lista os ZIP anuais com sha1 e data de versão (republicação semanal)
  └─ sincronizacao.py   plano diário: anos com sha1 alterado → atualizar; anos em falta → backfill
  │                     (do mais recente para o mais antigo, N por dia); lock contra execuções simultâneas
  └─ fontes/base_impic.py   descarga, verificação sha1, leitura JSON em streaming (memória constante)
      └─ normalizacao.py    parsing + avisos de qualidade; nunca completa dados em falta
          └─ PostgreSQL     COPY → dedup em SQL → upsert por (fonte, id_origem) com checksum por registo;
          │                 contratos que desaparecem da fonte são marcados (removido_da_fonte_em), nunca apagados
          └─ pontuacao.py + regras/ + calibracao.py   avaliação explicável e calibrada (só se algo mudou)
              └─ api.py (FastAPI) + static/   lista, CSV, /qualidade, /metodologia
agendador.py (APScheduler, 06:17 Europe/Lisbon) → sincronizacao.executar()
```

Decisão: **só PostgreSQL** (sem Neo4j por agora). A tabela `relacao` já é bitemporal
(`valido_de/valido_ate` = quando existiu; `registado_em/substituido_em` = quando o soubemos) e tem
`natureza` = `documentada` | `inferida` (inferidas exigem `confianca`). Reavaliar um motor de grafos na Fase 4
se a performance o justificar.

### Datas

`data_referencia` = data de celebração; se a fonte não a tem, data de publicação (ex.: 26 718 ajustes diretos
simplificados de 2020 ao abrigo do regime COVID, DL 10-A/2020, sem data de celebração). Os filtros de período usam-na.

## Modelo de dados (resumo)

- `entidade` — uma linha por ator; `chave` = NIF, ou `nome:<nome normalizado>` quando a fonte omite o NIF
  (`identificado_por` = `nif` | `nome`). Variantes de nome em `entidade_nome`.
- `contrato` (+ `contrato_adjudicatario`, `contrato_concorrente`) — guarda o registo original em `dados_origem`
  e liga ao detalhe em base.gov.pt (`url_fonte`). `n_concorrentes` NULL = desconhecido (≠ 0).
- `avaliacao_risco` — um resultado por contrato × indicador: `sinal | sem_sinal | nao_aplicavel | dados_insuficientes`,
  pontos, explicação e evidência.
- `pessoa`, `relacao` — preparadas para as Fases 4-5 (`pessoa.removido_em` para pedidos de remoção RGPD).
- `meta.sincronizacao` (uma por execução diária: plano e resumo), `meta.execucao_ingestao` (uma por ano carregado),
  `meta.resumo_qualidade` (avisos agregados por tipo, com exemplos), `meta.problema_qualidade` (rejeições e ids
  repetidos, um a um), `meta.cobertura` (estado por ano + versão da fonte). Visíveis em `/qualidade`.

## Indicadores

| Código | Tipo | Regra (resumo) | 2012–2026: sinais / avaliáveis |
|---|---|---|---|
| `concorrente_unico` | contrato | procedimento concorrencial com 1 concorrente | 127 431 / 446 151 (28,6 %) |
| `preco_acima_base` | contrato | preço contratual > preço base (+1 €) | 1 305 / 2,1 M (0,1 %) |
| `derrapagem_execucao` | contrato | preço efetivo ≥ 120 % do contratual | 14 845 / 792 970 (1,9 %) |
| `ajuste_direto_acima_limiar` | contrato | ajuste direto fundamentado no valor ≥ limiar da própria base legal | 6 772 / 747 858 (0,9 %) |
| `valor_logo_abaixo_limiar` | contrato | idem, entre 90 % e 100 % do limiar | 67 059 / 747 858 (9,0 %) |
| `publicacao_tardia` | contrato | publicado > 90 dias após celebração | 399 874 / 2,2 M (18 %) |
| `timing_eleitoral` | contrato | autarquia, 60 dias antes das autárquicas (só anos eleitorais) | 36 007 / 178 113 (20 %) |
| `ajuste_direto_repetido` | conjunto | mesmo adjudicante+fornecedor+base legal+CPV2, acumulado em 3 anos ≥ limiar (CCP art. 113.º) | 80 016 / 747 806 (10,7 %) |
| `fracionamento` | conjunto | ajustes diretos ao mesmo fornecedor, mesmo CPV3, ±30 dias, cada um < limiar e soma ≥ limiar | 12 934 / 741 034 (1,7 %) |
| `concentracao` | conjunto | fornecedor com ≥ 50 % do valor da entidade no setor/ano (≥ 5 contratos) | 70 157 / 1,8 M (3,9 %) |
| `fornecedor_estreante` | conjunto | contrato ≥ 100 000 € é o 1.º do fornecedor no BASE (desde 2014) | 2 727 / 183 085 (1,5 %) |
| `prazo_curto` | conjunto | prazo de propostas (com prorrogações) < percentil 10 de comparáveis | 8 257 / 225 087 (3,7 %) |

**Bloqueados por falta de fonte** (registo comercial): empresas recém-criadas (substituído por `fornecedor_estreante`)
e ligações societárias entre fornecedores. Descrições, limites e parâmetros de cada um: `contratacao/regras/` e
página `/metodologia`.

**Limiares legais (a validar juridicamente):** a base legal vem do campo `fundamentacao` de cada contrato (sql/005):
art. 19.º a)/20.º n.º 1 a) do CCP 2008 (150 000 € / 75 000 €) e art. 19.º d)/20.º n.º 1 d) do CCP 2017 (30 000 € / 20 000 €).
O campo `CritMateriais` da fonte **não** é usado: há milhares de contratos marcados "Não" que invocam o art. 24.º
(critérios materiais) — usá-lo gerava 53 mil falsos "ajustes diretos acima do limiar" (contra 6 772 com a base legal).

### Score da entidade

Média dos scores por indicador (0–100, ver calibração) nos indicadores com ≥ 5 contratos avaliáveis.
Níveis de triagem: alto 50–100, médio 20–49, baixo 0–19.

### Calibração (`contratacao/calibracao.py`)

- **Taxa de referência** por indicador e grupo comparável: (ano, procedimento, divisão CPV) → (ano, procedimento) →
  (procedimento); usa-se o primeiro grupo com ≥ 30 contratos avaliáveis. Guardadas em `referencia_risco`.
- **Pontos por contrato** = base × (1 − taxa). Ex. 2025: concurso público ≈ 9, consulta prévia ≈ 5.
- **Score de entidade (0–100)** por indicador: `100 × (L − p_esp) / (1 − p_esp)`, com `p_esp` = esperados/avaliáveis e
  `L` = limite inferior de Wilson (95 %) da taxa observada; 0 se `L ≤ p_esp` ou < 5 contratos avaliáveis.
  Independente da dimensão e penaliza amostras pequenas. Média dos indicadores ativos = score da entidade.
  A fórmula existe em Python (explicação, `/api/entidades/{id}/risco`) e em SQL (lista); um teste garante que coincidem.
- Efeito em 2025: a MEO (93 sinais vs 90,9 esperados) passa de 1.º lugar a score 0; no topo ficam entidades com
  taxas muito acima do esperado e amostra suficiente (ex.: 37/37 com 37 % esperado → 89).

## Achados da Fase 0 (2026-10-05)

**Fonte escolhida:** dataset dados.gov.pt *"Contratos Públicos - Portal Base - IMPIC - Contratos de 2012 a 2026"*.
Ficheiros anuais (ZIP com JSON e XLSX) 2012–2026, **republicados semanalmente** (última versão 2026-10-04).
Os URLs mudam a cada republicação → resolvidos pela API (`/api/1/datasets/<slug>/`).
Há também datasets de **Anúncios** (2012–2026) e **Modificações contratuais**, úteis para prazos e desvios (Fase 3).

**Rejeitado:** dataset *"OCDS - Portal BASE"* — parado desde 2022-10-27 (cobertura até 2022-01). Normalizamos nós.

**Perfil real do ano 2025** (247 725 registos, 39 campos):
- 348 `idcontrato` repetidos (diferem p.ex. só em `TipoCriterioAdjudicacao`) → mantém-se o último, com aviso.
- `concorrentes` vazio em 57 % dos registos; em concursos públicos ~36 % (13 883 de 39 043).
- 26 659 adjudicatários **sem NIF** na fonte (formato `"- - Nome"`): pessoas singulares e estrangeiras.
- 555 preços contratuais ≤ 0; 1 201 sem data de celebração; 2 rejeitados (adjudicante sem NIF).
- Entidades HTML no texto (`&amp;`), formato `NIF-Nome` sem espaços nos concorrentes.
- Publicação vs celebração: mediana 9 dias, p90 110 dias, 133 casos publicados antes da celebração (útil para o
  indicador de publicação tardia).
- Campos extra face ao que o projeto gov-analytics usava: `dataDecisaoAdjudicacao`, `regime`, `NUTs`, `Lotes`,
  `adjudicatarioPMEs`, `tipoFimContrato`, `linkPecasProc`…

**Resultado do 1.º indicador em 2025:** 19 334 sinais; 22 505 dados insuficientes. Taxa de concorrente único:
concurso público 12,6 %, consulta prévia 47,1 %. A soma simples de pontos favorecia fornecedores grandes →
**calibrado** (ver secção Calibração).

**Reaproveitado de [mopanc/gov-analytics](https://github.com/mopanc/gov-analytics) (MIT):** conhecimento do formato
(campos, "NIF - Nome", datas, CPV, local) e o padrão de execução de ingestão por checksum. Código não reaproveitado
(stack TypeScript); o parser deles descarta `concorrentes` e rejeições em silêncio.

**Outras fontes (por validar nas fases seguintes):** TED (API pública), Diário da República, Tribunal de Contas,
listas de sanções UE/OFAC — viáveis. **Registo comercial**: sem acesso aberto em massa (certidão permanente paga;
RCBE restrito desde o acórdão TJUE de 2022) → maior risco para pessoas/grafo; fazer prova de conceito antes da Fase 4.

## Resultado da Fase 2 (2026-10-05)

- **2 266 045 contratos** de 2012–2026 (15/15 anos), 213 072 entidades, BD com 6,7 GB.
- Backfill completo: ~15 min (14 anos); memória do processo ~170 MB (streaming).
- Sincronização diária sem alterações: ~1 s (só compara sha1; não descarrega nada).
- Teste real de resiliência: um ano falhou (bloqueio causado por uma migração concorrente, entretanto corrigida
  com o registo `meta.migracao`); os restantes continuaram e a execução seguinte recuperou-o sozinha.
- 103 501 entidades (quase metade) estão identificadas só por nome, porque a fonte omite o NIF — sobretudo
  pessoas singulares. Relevante para a resolução de entidades (Fase 4).
- ~~Pendente: lista lenta (~7 s)~~ → resolvido na Fase 3.

## Resultado da Fase 3 (2026-10-05)

- 12 indicadores, 144 testes (casos conhecidos por indicador; equivalência SQL ↔ Python do score e da calibração).
- Pontuação completa: ~6 min, **~100 MB de RAM** (antes 3,5 GB), resultados em ~1,5 GB (antes 5,2 GB); construída
  num schema auxiliar e trocada atomicamente.
- Lista: histórico completo ~0,5 s (resumo pré-calculado), um ano ~0,7 s, filtros finos (distrito, procedimento,
  CPV, valor, datas arbitrárias) 1–2,5 s.
- Nova fonte: anúncios de procedimento (2012–2026), para o indicador de prazos.
- **Minimização de dados pessoais:** 89 mil fornecedores que parecem pessoas singulares (sem NIF na fonte e sem forma
  jurídica no nome, ou NIF de herança/empresário individual) não são listados nem perfilados (sql/010).
- Anomalias da fonte registadas sem correção: p.ex. anúncio de 2020 com preço base de 34 000 000 000 000 000 €.

## Privacidade e RGPD

- Dados de contratação são públicos por lei; aplica-se minimização: não enriquecemos dados de pessoas singulares
  que sejam apenas fornecedores; a fonte já omite o NIF destas e nós não o tentamos recuperar.
- Pessoas (Fase 5): só quem exerce/exerceu cargo público ou societário relevante; só factos de registo público,
  cada um com fonte e data; nunca relações pessoais privadas.
- Relações **documentadas** e **inferidas** são sempre rotuladas e mostradas de forma distinta.
- Pedidos de correção/remoção: `pessoa.removido_em` oculta a pessoa em todas as vistas (processo e contacto a definir).
