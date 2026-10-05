"""Indicadores que olham para um único contrato."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from . import limiares
from .base import ContratoAvaliavel, Indicador, Resultado

AJUSTES_DIRETOS = ("ajuste_direto",)  # regime geral; o simplificado tem regras próprias
TOLERANCIA_EUR = Decimal("1")         # diferenças de arredondamento


def _eur(v: Decimal) -> str:
    return f"{v:,.2f} €".replace(",", " ").replace(".", ",")


class PrecoAcimaBase(Indicador):
    codigo = "preco_acima_base"
    nome = "Preço contratual acima do preço base"
    versao = "1.0"
    pontos = 10
    descricao = ("O preço contratual ultrapassa o preço base do procedimento — o valor máximo que a entidade "
                 "declarou estar disposta a pagar. Pode indicar especificações ou avaliação desenhadas à medida "
                 "ou alterações ao objeto.")
    limites = ("Pode haver justificações (revisão de preços, erros de registo, lotes agregados). Só avaliado quando a "
               "fonte indica preço base e preço contratual.")
    referencia = "CCP (preço base como preço máximo); Fazekas & Kocsis (2020), indicadores de preço."
    parametros = {"tolerancia_eur": float(TOLERANCIA_EUR)}

    def avaliar(self, c: ContratoAvaliavel) -> Resultado:
        if not c.preco_base or not c.preco_contratual or c.preco_contratual <= 0:
            return self._r("dados_insuficientes", "Preço base ou preço contratual em falta na fonte.")
        razao = c.preco_contratual / c.preco_base
        if c.preco_contratual > c.preco_base + TOLERANCIA_EUR:
            return self._r("sinal",
                           f"Preço contratual {_eur(c.preco_contratual)} acima do preço base {_eur(c.preco_base)} "
                           f"(+{(razao - 1):.0%}).",
                           preco_contratual=str(c.preco_contratual), preco_base=str(c.preco_base),
                           razao=round(float(razao), 4))
        return self._r("sem_sinal", f"Preço contratual dentro do preço base ({razao:.0%} do base).",
                       razao=round(float(razao), 4))


class DerrapagemExecucao(Indicador):
    codigo = "derrapagem_execucao"
    nome = "Preço final muito acima do contratado"
    versao = "1.0"
    pontos = 10
    LIMITE = Decimal("1.20")
    descricao = ("O preço total efetivo no fecho do contrato excede em 20 % ou mais o preço contratual. "
                 "Derrapagens grandes podem indicar propostas artificialmente baixas compensadas depois.")
    limites = ("Só existe preço efetivo para contratos já fechados e reportados (cerca de 1 em cada 5). "
               "Trabalhos complementares legítimos e revisões de preços também aumentam o valor final.")
    referencia = "Fazekas, Tóth & King (2016), indicadores de execução contratual."
    parametros = {"limite_razao": float(LIMITE)}

    def avaliar(self, c: ContratoAvaliavel) -> Resultado:
        if c.preco_efetivo is None:
            return self._r("nao_aplicavel", "Sem preço efetivo (contrato não fechado ou não reportado).")
        if not c.preco_contratual or c.preco_contratual <= 0:
            return self._r("dados_insuficientes", "Preço contratual em falta na fonte.")
        razao = c.preco_efetivo / c.preco_contratual
        if razao >= self.LIMITE:
            return self._r("sinal",
                           f"Preço efetivo {_eur(c.preco_efetivo)} é {razao:.0%} do contratado "
                           f"({_eur(c.preco_contratual)}).",
                           preco_efetivo=str(c.preco_efetivo), preco_contratual=str(c.preco_contratual),
                           razao=round(float(razao), 4))
        return self._r("sem_sinal", f"Preço efetivo é {razao:.0%} do contratado.", razao=round(float(razao), 4))


def _base_ajuste_direto(ind: Indicador, c: ContratoAvaliavel) -> tuple[Resultado | None, Decimal | None]:
    """Filtros comuns aos indicadores de limiar. Devolve (resultado_final, limiar)."""
    if c.procedimento not in AJUSTES_DIRETOS:
        return ind._r("nao_aplicavel", "Não é ajuste direto (regime geral)."), None
    if c.regime_tipo not in ("ccp2008", "ccp2017"):
        return ind._r("nao_aplicavel", "Regime especial ou regional, com limiares próprios."), None
    if c.fundamento_ad is None:
        return ind._r("dados_insuficientes", "Base legal do ajuste direto em falta na fonte."), None
    lim = limiares.limiar(c.regime_tipo, c.fundamento_ad)
    if lim is None:
        return ind._r("nao_aplicavel", "Ajuste direto não escolhido em função do valor (p.ex. critérios materiais)."), None
    if not c.preco_contratual or c.preco_contratual <= 0:
        return ind._r("dados_insuficientes", "Preço contratual em falta na fonte."), None
    return None, lim


class AjusteDiretoAcimaLimiar(Indicador):
    codigo = "ajuste_direto_acima_limiar"
    nome = "Ajuste direto acima do limiar legal"
    versao = "1.0"
    pontos = 10
    descricao = ("Ajuste direto cuja base legal invocada é o valor do contrato (CCP arts. 19.º/20.º), mas com preço "
                 "contratual igual ou superior ao limiar dessa mesma base legal.")
    limites = ("Limiares a validar juridicamente. Só avalia ajustes diretos fundamentados no valor e no regime geral "
               "do CCP; critérios materiais (urgência, exclusividade…), regimes especiais e Regiões Autónomas ficam "
               "de fora. Pode refletir erros de registo (base legal ou preço). Pressupõe preços sem IVA.")
    referencia = limiares.FONTE
    parametros = {f"{r}/{c}": float(v) for (r, c), v in limiares.LIMIAR_AJUSTE_DIRETO.items()}

    def avaliar(self, c: ContratoAvaliavel) -> Resultado:
        final, lim = _base_ajuste_direto(self, c)
        if final:
            return final
        if c.preco_contratual >= lim:
            return self._r("sinal",
                           f"Ajuste direto de {_eur(c.preco_contratual)} igual ou acima do limiar de {_eur(lim)} "
                           f"(base legal: {c.fundamento_ad}).",
                           preco_contratual=str(c.preco_contratual), limiar=str(lim))
        return self._r("sem_sinal", f"Abaixo do limiar de {_eur(lim)}.", limiar=str(lim))


class ValorLogoAbaixoLimiar(Indicador):
    codigo = "valor_logo_abaixo_limiar"
    nome = "Valor logo abaixo do limiar do ajuste direto"
    versao = "1.0"
    pontos = 10
    FAIXA = Decimal("0.90")
    descricao = ("Ajuste direto fundamentado no valor, com preço entre 90 % e 100 % do limiar legal. Uma concentração de contratos "
                 "colados ao limiar pode indicar valores ajustados para evitar procedimentos concorrenciais.")
    limites = ("Isoladamente é muito fraco: muitos contratos legítimos ficam perto do limite. Ganha significado no "
               "score da entidade (comparação com a taxa esperada). Mesmas exclusões que o indicador de limiar.")
    referencia = limiares.FONTE + "; Fazekas et al. (2016) sobre bunching junto a limiares."
    parametros = {"faixa_inferior": float(FAIXA)}

    def avaliar(self, c: ContratoAvaliavel) -> Resultado:
        final, lim = _base_ajuste_direto(self, c)
        if final:
            return final
        if lim * self.FAIXA <= c.preco_contratual < lim:
            return self._r("sinal",
                           f"Preço {_eur(c.preco_contratual)} = {c.preco_contratual / lim:.1%} do limiar de "
                           f"{_eur(lim)}.",
                           preco_contratual=str(c.preco_contratual), limiar=str(lim))
        return self._r("sem_sinal", f"Fora da faixa de 90–100 % do limiar de {_eur(lim)}.", limiar=str(lim))


class PublicacaoTardia(Indicador):
    codigo = "publicacao_tardia"
    nome = "Publicação tardia no Portal BASE"
    versao = "1.0"
    pontos = 10
    DIAS = 90
    descricao = ("O contrato foi publicado no Portal BASE mais de 90 dias depois de celebrado. A publicação é condição "
                 "de transparência (e, no ajuste direto, de eficácia); atrasos grandes reduzem o escrutínio.")
    limites = ("Os 90 dias são um parâmetro estatístico (≈ percentil 90 em 2025), não um prazo legal. Contratos "
               "nunca publicados não aparecem nos dados — a 'publicação em falta' não é observável aqui.")
    referencia = "Fazekas & Kocsis (2020), indicadores de transparência; CCP art. 127.º (publicitação)."
    parametros = {"dias": DIAS}

    def avaliar(self, c: ContratoAvaliavel) -> Resultado:
        if c.data_celebracao is None or c.data_publicacao is None:
            return self._r("dados_insuficientes", "Data de celebração ou de publicação em falta.")
        dias = (c.data_publicacao - c.data_celebracao).days
        if dias < 0:
            return self._r("dados_insuficientes", "Publicação anterior à celebração (incoerência da fonte).",
                           dias=dias)
        if dias > self.DIAS:
            return self._r("sinal", f"Publicado {dias} dias após a celebração (> {self.DIAS}).", dias=dias)
        return self._r("sem_sinal", f"Publicado {dias} dias após a celebração.", dias=dias)


# Eleições autárquicas (dia da votação). Fontes: rr.pt (03/07/2025, marcação para 12/10/2025);
# dnoticias.pt (01/09/2021, 26/09/2021); idealista.pt (2013: 29/09; 2017: 01/10).
AUTARQUICAS = (date(2013, 9, 29), date(2017, 10, 1), date(2021, 9, 26), date(2025, 10, 12))
PREFIXOS_AUTARQUIA = ("município", "municipio", "câmara municipal", "camara municipal", "freguesia",
                      "união das freguesias", "uniao das freguesias", "junta de freguesia")


def e_autarquia(nome: str | None) -> bool:
    return bool(nome) and nome.strip().lower().startswith(PREFIXOS_AUTARQUIA)


class TimingEleitoral(Indicador):
    codigo = "timing_eleitoral"
    nome = "Contrato de autarquia nos 60 dias antes de eleições autárquicas"
    versao = "1.0"
    pontos = 10
    DIAS = 60
    descricao = ("Contrato celebrado por um município ou freguesia nos 60 dias que antecedem eleições autárquicas. "
                 "Por contrato é um sinal fraco; o que interessa é o score da entidade: uma autarquia que concentra "
                 "nesse período muito mais contratos do que o esperado mostra um pico pré-eleitoral.")
    limites = ("Só avalia anos com eleições autárquicas (o resto do ano serve de comparação). "
               "Muitos contratos nesse período são normais (ciclo orçamental). Autarquias identificadas pelo nome "
               "(Município…, Freguesia…, União das Freguesias…); empresas municipais não estão incluídas.")
    referencia = "Fazekas (literatura sobre ciclos político-orçamentais na contratação)."
    parametros = {"dias_antes": DIAS, "eleicoes_autarquicas": [d.isoformat() for d in AUTARQUICAS]}

    def avaliar(self, c: ContratoAvaliavel) -> Resultado:
        if not e_autarquia(c.adjudicante_nome):
            return self._r("nao_aplicavel", "Adjudicante não é uma autarquia.")
        d = c.data_celebracao
        if d is None:
            return self._r("dados_insuficientes", "Data de celebração em falta.")
        eleicao = next((e for e in AUTARQUICAS if e.year == d.year), None)
        if eleicao is None:
            # só anos com eleições: compara a janela pré-eleitoral com o resto desse ano
            return self._r("nao_aplicavel", "Ano sem eleições autárquicas.")
        if eleicao - timedelta(days=self.DIAS) <= d < eleicao:
            return self._r("sinal", f"Celebrado {(eleicao - d).days} dias antes das autárquicas de "
                           f"{eleicao:%d/%m/%Y}.", eleicao=eleicao.isoformat(), dias_antes=(eleicao - d).days)
        return self._r("sem_sinal", f"Fora dos 60 dias anteriores às autárquicas de {eleicao:%d/%m/%Y}.")
