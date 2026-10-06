"""Grafo temporal de relações entre entidades (bitemporal).

Cada relação tem:
- período de VALIDADE no mundo real: [valido_de, valido_ate] — permite distinguir atual de histórica;
- período de CONHECIMENTO: [registado_em, substituido_em) — quando o sistema a soube e quando deixou
  de a considerar verdadeira nessa forma. Reconstruir não apaga nada: uma relação que muda gera uma
  nova versão e a anterior fica marcada como substituída.
- natureza: 'documentada' (facto da fonte) ou 'inferida' (dedução, sempre com `confianca`).

Relações construídas a partir dos contratos (fonte BASE):
- adjudicou_a (documentada): entidade adjudicante -> fornecedor; validade do 1.º contrato ao fim
  (real ou estimado) da execução do último.
- concorreram_juntos (documentada): dois fornecedores listados como concorrentes nos mesmos
  procedimentos pelo menos MIN_CO_CONCORRENCIA vezes; validade da 1.ª à última vez + 1 ano.
- possivelmente_mesma_entidade (inferida): empresa sem NIF na fonte cujo nome normalizado (sem forma
  jurídica) coincide com o de exatamente UMA entidade com NIF. Só para pessoas coletivas; o nome tem de
  ter ≥ 2 palavras e ≥ 8 caracteres (nomes curtos como "Boston" são ambíguos). Confiança 0,8 se coincide
  também a forma jurídica, 0,6 se não.

Uma relação é "atual" se o seu período de validade inclui hoje.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

import psycopg

from .config import FONTE_CONTRATOS
from .normalizacao import chave_nome

log = logging.getLogger(__name__)

MIN_CO_CONCORRENCIA = 5
VALIDADE_CO_CONCORRENCIA_DIAS = 365
CONFIANCA_MESMO_NOME = 0.6          # nome igual sem forma jurídica
CONFIANCA_MESMO_NOME_EXATO = 0.8    # nome normalizado igual, incluindo a forma jurídica

# formas jurídicas removidas para comparar nomes ("google ireland ltd" == "google ireland limited")
_FORMAS = re.compile(
    r"\b(lda|limitada|unipessoal|sa|s a|sgps|crl|ltd|limited|llc|plc|inc|incorporated|corp|corporation|gmbh|ag|"
    r"sl|slu|srl|sarl|sas|spa|bv|nv|doo|d o o|oy|ab|epe|e p e)\b")


def nome_suficiente(chave: str) -> bool:
    """Nomes curtos ou de uma só palavra são ambíguos demais para inferir identidade."""
    return len(chave) >= 8 and len(chave.split()) >= 2


def chave_sem_forma(nome: str) -> str:
    """Nome normalizado sem forma jurídica (para a relação inferida 'possivelmente_mesma_entidade')."""
    return " ".join(_FORMAS.sub(" ", chave_nome(nome)).split())


_SQL_DOCUMENTADAS = f"""
INSERT INTO rel_nova (origem_id, destino_id, tipo, natureza, confianca, valido_de, valido_ate, fonte_data, detalhe)
SELECT k.adjudicante_id, ca.entidade_id, 'adjudicou_a', 'documentada', NULL,
       min(k.data_referencia), max(coalesce(k.data_fim_estimada, k.data_referencia)), max(k.data_publicacao),
       jsonb_build_object('n_contratos', count(*), 'total', round(coalesce(sum(k.preco_contratual), 0), 2),
                          'ultimo_contrato', max(k.data_referencia))
FROM contrato k JOIN contrato_adjudicatario ca ON ca.contrato_id = k.id
WHERE k.data_referencia IS NOT NULL AND k.fonte = '{FONTE_CONTRATOS}'
GROUP BY 1, 2;

-- pares de concorrentes por procedimento (lotes do mesmo procedimento contam uma vez)
CREATE TEMP TABLE conc_proc ON COMMIT DROP AS
SELECT DISTINCT coalesce(k.id_procedimento, 'k' || k.id) AS proc, cc.entidade_id, k.data_referencia
FROM contrato_concorrente cc JOIN contrato k ON k.id = cc.contrato_id
WHERE k.data_referencia IS NOT NULL;
CREATE INDEX ON conc_proc (proc);

INSERT INTO rel_nova (origem_id, destino_id, tipo, natureza, confianca, valido_de, valido_ate, fonte_data, detalhe)
SELECT a.entidade_id, b.entidade_id, 'concorreram_juntos', 'documentada', NULL,
       min(a.data_referencia), max(a.data_referencia) + {VALIDADE_CO_CONCORRENCIA_DIAS}, max(a.data_referencia),
       jsonb_build_object('n_procedimentos', count(DISTINCT a.proc), 'ultima_vez', max(a.data_referencia))
FROM conc_proc a JOIN conc_proc b ON b.proc = a.proc AND b.entidade_id > a.entidade_id
GROUP BY 1, 2
HAVING count(DISTINCT a.proc) >= {MIN_CO_CONCORRENCIA};
"""


def _inferidas_mesmo_nome(con: psycopg.Connection) -> list[tuple]:
    """Empresas sem NIF -> entidade com NIF com o mesmo nome normalizado (sem forma jurídica)."""
    por_chave: dict[str, set[int]] = defaultdict(set)
    exatos: dict[str, set[int]] = defaultdict(set)
    cur = con.cursor(name="nomes_nif")
    cur.itersize = 50_000
    cur.execute("SELECT n.entidade_id, n.nome FROM entidade_nome n JOIN entidade e ON e.id = n.entidade_id "
                "WHERE e.identificado_por = 'nif'")
    for l in cur:
        k = chave_sem_forma(l["nome"])
        if nome_suficiente(k):
            por_chave[k].add(l["entidade_id"])
            exatos[chave_nome(l["nome"])].add(l["entidade_id"])
    cur.close()
    out = []
    for l in con.execute("SELECT id, nome, primeiro_visto, ultimo_visto FROM entidade "
                         "WHERE identificado_por = 'nome' AND tipo_pessoa = 'coletiva_sem_nif'"):
        k = chave_sem_forma(l["nome"])
        alvos = por_chave.get(k, set()) if nome_suficiente(k) else set()
        if len(alvos) == 1:
            alvo = next(iter(alvos))
            confianca = (CONFIANCA_MESMO_NOME_EXATO if alvo in exatos.get(chave_nome(l["nome"]), ())
                         else CONFIANCA_MESMO_NOME)
            out.append((l["id"], alvo, "possivelmente_mesma_entidade", "inferida", confianca,
                        l["primeiro_visto"], None, None,
                        json.dumps({"metodo": "nome normalizado igual (sem forma jurídica); a entidade sem NIF "
                                              "não tem NIF na fonte", "nome_sem_nif": l["nome"]},
                                   ensure_ascii=False)))
    return out


def reconstruir(con: psycopg.Connection) -> dict[str, int]:
    """Recalcula as relações e aplica as diferenças de forma bitemporal. Devolve contadores."""
    cur = con.cursor()
    gid = cur.execute("INSERT INTO meta.grafo DEFAULT VALUES RETURNING id").fetchone()["id"]
    con.commit()
    cur.execute(
        "CREATE TEMP TABLE rel_nova (origem_id bigint, destino_id bigint, tipo text, natureza text, "
        "confianca numeric(3,2), valido_de date, valido_ate date, fonte_data date, detalhe jsonb) ON COMMIT DROP"
    )
    cur.execute(_SQL_DOCUMENTADAS)
    inferidas = _inferidas_mesmo_nome(con)  # antes do COPY: a ligação não aceita consultas durante um COPY
    with cur.copy("COPY rel_nova FROM STDIN") as cp:
        for linha in inferidas:
            cp.write_row(linha)
    cur.execute("CREATE INDEX ON rel_nova (origem_id, destino_id, tipo)")

    # 1) versões correntes que deixaram de existir ou mudaram -> substituídas
    substituidas = cur.execute(
        """
        UPDATE relacao r SET substituido_em = clock_timestamp()
        WHERE r.substituido_em IS NULL AND r.fonte = %(f)s AND r.origem_tipo = 'entidade' AND r.destino_tipo = 'entidade'
          AND NOT EXISTS (
            SELECT 1 FROM rel_nova n
            WHERE n.origem_id = r.origem_id AND n.destino_id = r.destino_id AND n.tipo = r.tipo
              AND n.natureza = r.natureza AND n.confianca IS NOT DISTINCT FROM r.confianca
              AND n.valido_de IS NOT DISTINCT FROM r.valido_de AND n.valido_ate IS NOT DISTINCT FROM r.valido_ate
              AND n.detalhe IS NOT DISTINCT FROM r.detalhe)
        RETURNING 1
        """, {"f": FONTE_CONTRATOS}).rowcount
    # 2) relações novas ou alteradas -> nova versão corrente
    inseridas = cur.execute(
        """
        INSERT INTO relacao (origem_tipo, origem_id, destino_tipo, destino_id, tipo, natureza, confianca,
                             valido_de, valido_ate, fonte, fonte_data, detalhe)
        SELECT 'entidade', n.origem_id, 'entidade', n.destino_id, n.tipo, n.natureza, n.confianca,
               n.valido_de, n.valido_ate, %(f)s, n.fonte_data, n.detalhe
        FROM rel_nova n
        WHERE NOT EXISTS (
            SELECT 1 FROM relacao r
            WHERE r.substituido_em IS NULL AND r.fonte = %(f)s AND r.origem_tipo = 'entidade'
              AND r.origem_id = n.origem_id AND r.destino_tipo = 'entidade' AND r.destino_id = n.destino_id
              AND r.tipo = n.tipo)
        """, {"f": FONTE_CONTRATOS}).rowcount
    por_tipo = {l["tipo"]: l["n"] for l in cur.execute(
        "SELECT tipo, count(*) AS n FROM relacao WHERE substituido_em IS NULL GROUP BY 1")}
    resumo = {"inseridas": inseridas, "substituidas": substituidas, "correntes_por_tipo": por_tipo}
    cur.execute("UPDATE meta.grafo SET terminado_em = clock_timestamp(), resumo = %s WHERE id = %s",
                (json.dumps(resumo), gid))
    con.commit()
    con.execute("ANALYZE relacao")
    con.commit()
    log.info("Grafo reconstruído: %s", resumo)
    return resumo
