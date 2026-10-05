"""Normalização dos registos de contratos do Portal BASE (IMPIC).

Formato de origem: ficheiros anuais "contratosAAAA.zip" (JSON) publicados em
dados.gov.pt no dataset "Contratos Públicos - Portal Base - IMPIC - Contratos
de 2012 a 2026". Os nomes dos campos foram documentados a partir do parser do
projeto mopanc/gov-analytics (MIT) e TÊM de ser confirmados contra um ficheiro
real antes de confiarmos nos resultados (ver README, secção "Por confirmar").

Princípio: nunca inventar nem completar dados. Um campo ausente fica None e
gera um aviso de qualidade; um registo inutilizável é rejeitado COM motivo
(nunca descartado em silêncio).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ator:
    """Ator tal como aparece na fonte.

    A fonte omite o NIF de alguns adjudicatários (formato "- - Nome"): na prática
    pessoas singulares e empresas estrangeiras. Nesses casos nif=None e a
    identificação é feita só pelo nome, com confiança baixa (ver `chave`).
    """

    nif: str | None
    nome: str
    nif_valido: bool  # dígito de controlo português válido

    @property
    def chave(self) -> str:
        """Chave de resolução: NIF quando existe; senão nome normalizado (marcado)."""
        if self.nif:
            return self.nif
        return "nome:" + chave_nome(self.nome)


@dataclass
class ContratoNormalizado:
    id_origem: str
    id_procedimento: str | None
    adjudicante: Ator
    adjudicatarios: list[Ator]
    concorrentes: list[Ator] | None  # None = campo ausente/vazio na fonte (desconhecido)
    tipo_contrato: str | None
    procedimento: str  # chave normalizada (ver PROCEDIMENTOS)
    procedimento_original: str | None
    objeto: str | None
    data_publicacao: date | None
    data_celebracao: date | None
    data_decisao_adjudicacao: date | None
    preco_contratual: Decimal | None
    preco_base: Decimal | None
    preco_efetivo: Decimal | None
    cpv: str | None
    cpv_descricao: str | None
    distrito: str | None
    concelho: str | None
    fundamento_ajuste_direto: str | None
    regime: str | None
    criterio_adjudicacao: str | None
    ano: int | None
    checksum: str
    avisos: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rejeicao:
    id_origem: str | None
    motivo: str


# ---------------------------------------------------------------------------
# Utilitários de parsing
# ---------------------------------------------------------------------------

_NULOS = {"", "null", "none", "n/a", "-"}


def _vazio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and valor.strip().lower() in _NULOS)


def nif_valido(nif: str) -> bool:
    """Valida o dígito de controlo de um NIF/NIPC português (9 dígitos, módulo 11)."""
    if not re.fullmatch(r"\d{9}", nif):
        return False
    soma = sum(int(d) * (9 - i) for i, d in enumerate(nif[:8]))
    controlo = 11 - soma % 11
    if controlo >= 10:
        controlo = 0
    return controlo == int(nif[8])


_RE_NIF_NOME = re.compile(r"^\s*(\d+)\s*-\s*(.+?)\s*$", re.DOTALL)
_RE_SEM_NIF = re.compile(r"^\s*-\s*-?\s*(.+?)\s*$", re.DOTALL)


def chave_nome(nome: str) -> str:
    """Nome normalizado para comparação: sem acentos, minúsculas, sem pontuação."""
    s = _sem_acentos(html.unescape(nome)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def parse_ator(bruto: Any) -> Ator | None:
    """Separa "NIF - Nome" / "NIF-Nome". Aceita "- - Nome" (sem NIF na fonte).

    Devolve None se nem NIF nem nome forem identificáveis.
    """
    if _vazio(bruto) or not isinstance(bruto, str):
        return None
    texto = html.unescape(bruto)
    m = _RE_NIF_NOME.match(texto)
    if m:
        nif, nome = m.group(1), " ".join(m.group(2).split())
        return Ator(nif=nif, nome=nome, nif_valido=nif_valido(nif))
    m = _RE_SEM_NIF.match(texto)
    if m and m.group(1).strip(" -"):
        return Ator(nif=None, nome=" ".join(m.group(1).split()), nif_valido=False)
    return None


def parse_data(bruto: Any) -> date | None:
    """Aceita "DD/MM/AAAA" (formato BASE) e "AAAA-MM-DD"."""
    if _vazio(bruto) or not isinstance(bruto, str):
        return None
    s = bruto.strip()
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)
    try:
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return None


def parse_valor(bruto: Any) -> Decimal | None:
    """Valores monetários. Aceita número ou texto PT ("1.234,56")."""
    if _vazio(bruto):
        return None
    if isinstance(bruto, (int, float)):
        return Decimal(str(bruto))
    s = str(bruto).strip().replace("€", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _sem_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


# Chaves normalizadas de procedimento. "competitivo" = há concorrência aberta
# ou por convite a vários operadores (relevante para o indicador de concorrente único).
PROCEDIMENTOS: dict[str, dict[str, Any]] = {
    "ajuste_direto": {"rotulo": "Ajuste direto", "competitivo": False},
    "ajuste_direto_simplificado": {"rotulo": "Ajuste direto (regime simplificado)", "competitivo": False},
    "consulta_previa": {"rotulo": "Consulta prévia", "competitivo": True},
    "concurso_publico": {"rotulo": "Concurso público", "competitivo": True},
    "concurso_publico_urgente": {"rotulo": "Concurso público urgente", "competitivo": True},
    "concurso_limitado": {"rotulo": "Concurso limitado por prévia qualificação", "competitivo": True},
    "negociacao": {"rotulo": "Procedimento de negociação", "competitivo": True},
    "dialogo_concorrencial": {"rotulo": "Diálogo concorrencial", "competitivo": True},
    "parceria_inovacao": {"rotulo": "Parceria para a inovação", "competitivo": True},
    "concurso_concecao": {"rotulo": "Concurso de conceção", "competitivo": True},
    "consulta_previa_simplificada": {"rotulo": "Consulta prévia simplificada", "competitivo": True},
    "concurso_publico_simplificado": {"rotulo": "Concurso público simplificado", "competitivo": True},
    "concurso_limitado_simplificado": {"rotulo": "Concurso limitado simplificado", "competitivo": True},
    "acordo_quadro": {"rotulo": "Ao abrigo de acordo-quadro", "competitivo": False},
    "contratacao_excluida": {"rotulo": "Contratação excluída (fora do CCP)", "competitivo": False},
    "setores_especiais_isencao": {"rotulo": "Setores especiais – isenção", "competitivo": False},
    "servicos_sociais": {"rotulo": "Serviços sociais e outros serviços específicos", "competitivo": False},
    "outro": {"rotulo": "Outro / não classificado", "competitivo": False},
}

# Ordem importa: prefixos mais específicos primeiro.
_MAPA_PROCEDIMENTO: list[tuple[str, str]] = [
    ("ajuste direto regime simplificado", "ajuste_direto_simplificado"),
    ("ajuste direto simplificado", "ajuste_direto_simplificado"),
    ("ajuste direto regime geral", "ajuste_direto"),
    ("ajuste direto", "ajuste_direto"),
    ("ajuste directo", "ajuste_direto"),
    ("consulta previa simplificada", "consulta_previa_simplificada"),
    ("consulta previa", "consulta_previa"),
    ("concurso publico urgente", "concurso_publico_urgente"),
    ("concurso publico simplificado", "concurso_publico_simplificado"),
    ("concurso publico", "concurso_publico"),
    ("concurso limitado por previa qualificacao simplificado", "concurso_limitado_simplificado"),
    ("concurso limitado", "concurso_limitado"),
    ("procedimento de negociacao", "negociacao"),
    ("negociacao", "negociacao"),
    ("dialogo concorrencial", "dialogo_concorrencial"),
    ("parceria para a inovacao", "parceria_inovacao"),
    ("concurso de concecao", "concurso_concecao"),
    ("concurso de conceccao", "concurso_concecao"),
    ("concurso de ideias", "concurso_concecao"),  # variante do concurso de conceção
    ("ao abrigo de acordo", "acordo_quadro"),
    ("acordo-quadro", "acordo_quadro"),
    ("acordo quadro", "acordo_quadro"),
    ("contratacao excluida", "contratacao_excluida"),
    ("setores especiais", "setores_especiais_isencao"),
    ("servicos sociais", "servicos_sociais"),
]


def normalizar_procedimento(bruto: Any) -> str:
    if _vazio(bruto) or not isinstance(bruto, str):
        return "outro"
    s = " ".join(_sem_acentos(bruto).lower().replace("–", "-").split())
    for prefixo, chave in _MAPA_PROCEDIMENTO:
        if s.startswith(prefixo):
            return chave
    return "outro"


def parse_cpv(lista: Any) -> tuple[str | None, str | None]:
    """Primeiro CPV de ["XXXXXXXX-X - Descrição", ...]."""
    if not lista:
        return None, None
    primeiro = lista[0] if isinstance(lista, list) else lista
    if not isinstance(primeiro, str):
        return None, None
    m = re.match(r"^\s*(\d{8})(?:-\d)?\s*(?:-\s*(.+))?$", primeiro)
    if not m:
        return None, None
    return m.group(1), (m.group(2) or None)


def parse_local(lista: Any) -> tuple[str | None, str | None]:
    """"Portugal, Distrito, Concelho" -> (distrito, concelho). Fora de PT -> (None, None)."""
    if not lista:
        return None, None
    primeiro = lista[0] if isinstance(lista, list) else lista
    if not isinstance(primeiro, str):
        return None, None
    partes = [p.strip() for p in primeiro.split(",")]
    if not partes or partes[0].lower() != "portugal":
        return None, None
    distrito = partes[1] if len(partes) > 1 and partes[1] else None
    concelho = partes[2] if len(partes) > 2 and partes[2] else None
    return distrito, concelho


def _lista(valor: Any) -> list[Any]:
    if _vazio(valor):
        return []
    if isinstance(valor, list):
        return valor
    return [valor]


def _texto(valor: Any) -> str | None:
    if _vazio(valor):
        return None
    return " ".join(html.unescape(str(valor)).split())


def checksum_registo(bruto: dict[str, Any]) -> str:
    """Hash estável do registo inteiro: deteta alterações entre republicações."""
    canon = json.dumps(bruto, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Normalização de um registo
# ---------------------------------------------------------------------------


def normalizar_contrato(bruto: dict[str, Any]) -> ContratoNormalizado | Rejeicao:
    id_origem = _texto(bruto.get("idcontrato"))
    if not id_origem:
        return Rejeicao(None, "sem idcontrato")

    adjudicantes = [a for a in (parse_ator(x) for x in _lista(bruto.get("adjudicante"))) if a]
    if not adjudicantes:
        return Rejeicao(id_origem, "adjudicante ausente ou sem NIF")

    avisos: list[str] = []
    if len(adjudicantes) > 1:
        avisos.append(f"{len(adjudicantes)} adjudicantes; usado o primeiro")
    adjudicante = adjudicantes[0]
    if adjudicante.nif is None:
        return Rejeicao(id_origem, "adjudicante sem NIF")
    if not adjudicante.nif_valido:
        avisos.append(f"NIF de adjudicante com dígito de controlo inválido: {adjudicante.nif}")

    adjudicatarios = [a for a in (parse_ator(x) for x in _lista(bruto.get("adjudicatarios"))) if a]
    if not adjudicatarios:
        avisos.append("sem adjudicatário identificável")
    for a in adjudicatarios:
        if a.nif is None:
            avisos.append("adjudicatário sem NIF na fonte (identificado só por nome)")
        elif not a.nif_valido:
            avisos.append(f"NIF de adjudicatário com dígito de controlo inválido: {a.nif}")

    concorrentes_brutos = _lista(bruto.get("concorrentes"))
    concorrentes: list[Ator] | None
    if concorrentes_brutos:
        concorrentes = [a for a in (parse_ator(x) for x in concorrentes_brutos) if a]
        if len(concorrentes) != len(concorrentes_brutos):
            avisos.append("concorrentes com formato não reconhecido")
    else:
        concorrentes = None  # desconhecido, NÃO é "zero concorrentes"
        avisos.append("campo concorrentes vazio")

    preco = parse_valor(bruto.get("precoContratual"))
    if preco is None:
        avisos.append("preço contratual ausente")
    elif preco <= 0:
        avisos.append(f"preço contratual não positivo: {preco}")

    data_celebracao = parse_data(bruto.get("dataCelebracaoContrato"))
    if data_celebracao is None:
        avisos.append("data de celebração ausente/ilegível")

    cpv, cpv_desc = parse_cpv(bruto.get("cpv"))
    distrito, concelho = parse_local(bruto.get("localExecucao"))
    preco_base = parse_valor(bruto.get("precoBaseProcedimento"))
    preco_efetivo = parse_valor(bruto.get("PrecoTotalEfetivo"))

    ano = bruto.get("Ano")
    try:
        ano = int(ano) if not _vazio(ano) else None
    except (TypeError, ValueError):
        ano = None
    if ano is None and data_celebracao:
        ano = data_celebracao.year

    tipos = _lista(bruto.get("tipoContrato"))

    return ContratoNormalizado(
        id_origem=id_origem,
        id_procedimento=_texto(bruto.get("idprocedimento")),
        adjudicante=adjudicante,
        adjudicatarios=adjudicatarios,
        concorrentes=concorrentes,
        tipo_contrato=_texto(tipos[0]) if tipos else None,
        procedimento=normalizar_procedimento(bruto.get("tipoprocedimento")),
        procedimento_original=_texto(bruto.get("tipoprocedimento")),
        objeto=_texto(bruto.get("objectoContrato")) or _texto(bruto.get("descContrato")),
        data_publicacao=parse_data(bruto.get("dataPublicacao")),
        data_celebracao=data_celebracao,
        data_decisao_adjudicacao=parse_data(bruto.get("dataDecisaoAdjudicacao")),
        preco_contratual=preco,
        preco_base=preco_base if preco_base and preco_base > 0 else None,
        preco_efetivo=preco_efetivo if preco_efetivo and preco_efetivo > 0 else None,
        cpv=cpv,
        cpv_descricao=cpv_desc,
        distrito=distrito,
        concelho=concelho,
        fundamento_ajuste_direto=_texto(bruto.get("fundamentAjusteDireto")),
        regime=_texto(bruto.get("regime")),
        criterio_adjudicacao=_texto(bruto.get("TipoCriterioAdjudicacao")),
        ano=ano,
        checksum=checksum_registo(bruto),
        avisos=avisos,
    )
