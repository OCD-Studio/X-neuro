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
| 2 — Ingestão completa, scheduler diário, backfill, qualidade | ⏳ |
| 3 — Todos os indicadores + lista completa | ⏳ |
| 4 — Resolução de entidades + grafo temporal + perfis de entidade | ⏳ |
| 5 — Perfis de pessoa + organigrama | ⏳ |
| 6 — Alertas + polish | ⏳ |

## Arranque rápido

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user@localhost/contratacao   # PostgreSQL >= 14
python -m contratacao.cli migrar
python -m contratacao.cli ingerir --ano 2025        # descarrega de dados.gov.pt (~55 MB)
python -m contratacao.cli pontuar
uvicorn contratacao.api:app --port 8000             # http://localhost:8000
```

Testes: `pytest` (o teste de integração corre se `TEST_DATABASE_URL` apontar para uma BD **descartável** —
o teste apaga os schemas).

## Arquitetura (atual)

```
dados.gov.pt (ZIP/JSON anual, republicado semanalmente)
  └─ fontes/base_impic.py   resolve URL pela API, descarrega, SHA-256 (ficheiro igual = ignorado)
      └─ normalizacao.py    parsing + avisos de qualidade; nunca completa dados em falta
          └─ PostgreSQL     COPY + upsert por (fonte, id_origem) com checksum por registo
              └─ pontuacao.py + regras/   avaliação explicável por contrato
                  └─ api.py (FastAPI) + static/   lista ordenável/filtrável, CSV, metodologia
```

Decisão: **só PostgreSQL** (sem Neo4j por agora). A tabela `relacao` já é bitemporal
(`valido_de/valido_ate` = quando existiu; `registado_em/substituido_em` = quando o soubemos) e tem
`natureza` = `documentada` | `inferida` (inferidas exigem `confianca`). Reavaliar um motor de grafos na Fase 4
se a performance o justificar.

## Modelo de dados (resumo)

- `entidade` — uma linha por ator; `chave` = NIF, ou `nome:<nome normalizado>` quando a fonte omite o NIF
  (`identificado_por` = `nif` | `nome`). Variantes de nome em `entidade_nome`.
- `contrato` (+ `contrato_adjudicatario`, `contrato_concorrente`) — guarda o registo original em `dados_origem`
  e liga ao detalhe em base.gov.pt (`url_fonte`). `n_concorrentes` NULL = desconhecido (≠ 0).
- `avaliacao_risco` — um resultado por contrato × indicador: `sinal | sem_sinal | nao_aplicavel | dados_insuficientes`,
  pontos, explicação e evidência.
- `pessoa`, `relacao` — preparadas para as Fases 4-5 (`pessoa.removido_em` para pedidos de remoção RGPD).
- `meta.execucao_ingestao`, `meta.problema_qualidade`, `meta.cobertura` — auditoria, relatório de qualidade
  (`/api/qualidade`) e cobertura histórica (mostrada no topo da app).

## Indicadores

| Código | Pontos | Regra |
|---|---|---|
| `concorrente_unico` | 10 (base) | Procedimento concorrencial com exatamente 1 concorrente na fonte. Ajuste direto, acordo-quadro, contratação excluída → não aplicável. Lista de concorrentes vazia → dados insuficientes (não pontua). |

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

## Privacidade e RGPD

- Dados de contratação são públicos por lei; aplica-se minimização: não enriquecemos dados de pessoas singulares
  que sejam apenas fornecedores; a fonte já omite o NIF destas e nós não o tentamos recuperar.
- Pessoas (Fase 5): só quem exerce/exerceu cargo público ou societário relevante; só factos de registo público,
  cada um com fonte e data; nunca relações pessoais privadas.
- Relações **documentadas** e **inferidas** são sempre rotuladas e mostradas de forma distinta.
- Pedidos de correção/remoção: `pessoa.removido_em` oculta a pessoa em todas as vistas (processo e contacto a definir).
