# Instalar no Krystal (cPanel)

Guia passo-a-passo para pôr o sistema a correr no alojamento Krystal, com atualização diária automática.
Tempo estimado: 30–45 minutos + algumas horas (ou dias) para o histórico completo entrar.

> **Antes de abrir ao público:** os indicadores de limiar legal ainda precisam de validação jurídica e a lista
> nomeia empresas e entidades. Recomendo proteger o site com palavra-passe (passo 7) até essa validação.

## 0. O que é preciso

| Requisito | Onde ver |
|---|---|
| PostgreSQL **12 ou mais recente** | cPanel → *PostgreSQL Databases* (a versão aparece no topo ou em *Server Information*) |
| ~10 GB de quota de disco livres | cPanel → barra lateral *Disk Usage* |
| *Setup Python App* com Python 3.10+ | cPanel → *Setup Python App* |
| *Cron Jobs* | cPanel → *Cron Jobs* |

O passo 4 tem um comando que verifica tudo isto automaticamente.

## 1. Criar a base de dados

1. cPanel → **PostgreSQL Databases**.
2. *Create New Database*: nome `contratacao` (o cPanel acrescenta um prefixo, p.ex. `ocd_contratacao`).
3. *Add New User*: nome `contratacao`, palavra-passe forte (guardar num gestor de passwords).
4. *Add User to Database*: juntar o utilizador à base de dados, com **todos os privilégios**.

Fica com um endereço deste tipo (substituir os valores reais):

```
postgresql://ocd_contratacao:PALAVRA_PASSE@localhost/ocd_contratacao
```

## 2. Enviar o código

Opção simples (sem linha de comandos):
1. No GitHub, descarregar o repositório em ZIP (*Code → Download ZIP*).
2. Do ZIP, só interessa a pasta `inteligencia-contratacao`.
3. cPanel → **File Manager** → criar a pasta `contratacao` na pasta pessoal (fora de `public_html`) e fazer
   *Upload* do conteúdo de `inteligencia-contratacao` para lá (pode enviar em ZIP e usar *Extract*).

Opção com Git: cPanel → **Git™ Version Control** → *Create* → clonar o repositório (exige chave de acesso se
for privado) e apontar a app para a subpasta `inteligencia-contratacao`.

## 3. Criar a aplicação Python

cPanel → **Setup Python App** → *Create Application*:

| Campo | Valor |
|---|---|
| Python version | a mais recente disponível (≥ 3.10) |
| Application root | `contratacao` |
| Application URL | o domínio ou subdomínio escolhido (p.ex. `contratacao.ocdstudio.pt`) |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

*Create*.

Depois, no **File Manager**, dentro da pasta `contratacao`, criar o ficheiro `.env` (ativar *Show Hidden Files*
nas definições do File Manager para o ver) com:

```
DATABASE_URL=postgresql://ocd_contratacao:PALAVRA_PASSE@localhost/ocd_contratacao
DIR_DADOS=/home/UTILIZADOR/contratacao_tmp
APP_URL=https://SUBDOMINIO.ocd.pt
ADMIN_TOKEN=UMA_FRASE_LONGA_E_ALEATORIA
```

(`APP_URL` é usado nas ligações dos resumos de alertas; `ADMIN_TOKEN` permite marcar resumos como revistos na
página `/alertas` — sem ele, essa revisão fica desativada.)

e dar-lhe permissões `600` (botão direito → *Change Permissions*), porque contém a palavra-passe.
A app e o cron leem este ficheiro (as variáveis do ecrã *Setup Python App* não chegam aos Cron Jobs). Depois, na mesma página: em *Configuration files* escrever `requirements.txt` → **Run Pip Install**.

No topo da página aparece um comando do tipo
`source /home/UTILIZADOR/virtualenv/contratacao/3.11/bin/activate && cd /home/UTILIZADOR/contratacao` —
copiar: é usado nos passos seguintes.

## 4. Verificar e preparar a base de dados

cPanel → **Terminal** (se não existir, pedir acesso SSH ao suporte Krystal). Colar o comando copiado no passo 3 e depois:

```bash
python -m contratacao.cli verificar     # deve terminar com "Tudo pronto."
python -m contratacao.cli migrar        # cria as tabelas
```

Se `verificar` mostrar **FALHA** no PostgreSQL (versão < 12) ou no acesso a dados.gov.pt, parar aqui e
falar com o suporte Krystal (ou optar por uma VPS).

Abrir o endereço da app no browser: deve aparecer a lista vazia, com “Cobertura histórica: nenhuma”.

## 5. Atualização diária automática

cPanel → **Cron Jobs** → *Add New Cron Job*:

- *Common settings*: uma vez por dia; Minuto `17`, Hora `6` (atenção: o relógio do servidor pode estar em UTC;
  6:17 UTC = 7:17 em Lisboa no verão).
- *Command* (uma só linha; substituir UTILIZADOR e a versão do Python pelos do passo 3):

```
cd /home/UTILIZADOR/contratacao && /home/UTILIZADOR/virtualenv/contratacao/3.11/bin/python -m contratacao.cli sincronizar --max-backfill 2 >> /home/UTILIZADOR/contratacao_sync.log 2>&1; /home/UTILIZADOR/virtualenv/contratacao/3.11/bin/python -m contratacao.cli alertas >> /home/UTILIZADOR/contratacao_sync.log 2>&1
```

O segundo comando gera o **resumo diário de alertas como rascunho** (página `/alertas`). Nada é enviado: revê-se,
marca-se como revisto (com o `ADMIN_TOKEN` do `.env`) e só então se copia o texto.

Cada execução: verifica se a fonte mudou (≈ 1 s se nada mudou), atualiza o que mudou e carrega até **2 anos de
histórico** em falta. O histórico completo (15 anos) entra em ~8 dias, sem sobrecarregar o alojamento partilhado.
Para acelerar, pode correr à mão no Terminal `python -m contratacao.cli sincronizar --max-backfill 3`
algumas vezes (cada ano demora 1–3 minutos; o recálculo do score ~10 minutos).

**Segundo cron — cargos públicos (Entidade para a Transparência)**, uma vez por dia, p.ex. às `7:41`:

```
cd /home/UTILIZADOR/contratacao && /home/UTILIZADOR/virtualenv/contratacao/3.11/bin/python -m contratacao.cli cargos --max 500 >> /home/UTILIZADOR/contratacao_cargos.log 2>&1
```

Cada execução atualiza os 500 titulares recolhidos há mais tempo (~13 min a 1 pedido por segundo) e recalcula
cargos e relações. A primeira recolha completa (~12 400 titulares) é mais rápida feita à mão no Terminal, em
blocos: `python -m contratacao.cli cargos --max 3000` (retoma onde parou).

## 6. Confirmar

- Página **Qualidade dos dados** (`/qualidade`): anos carregados a verde, histórico de execuções.
- Ficheiro `contratacao_sync.log` na pasta pessoal: registo de cada execução.

## 7. Proteger com palavra-passe (recomendado até validação jurídica)

cPanel → **Directory Privacy** → escolher a pasta do domínio da app → *Password protect this directory* →
criar utilizador e palavra-passe. (Em alguns alojamentos com apps Python é preciso proteger a pasta
`public_html/<subdomínio>`; se não funcionar, pedir ao suporte para ativar autenticação básica no subdomínio.)

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| Página "Incomplete response" / erro 500 | `Run Pip Install` não correu, ou `.env` em falta/errado → ver *Setup Python App → Restart* e o log de erros |
| `verificar`: PostgreSQL < 12 | o alojamento não suporta colunas geradas; precisa de PostgreSQL mais recente (suporte Krystal) ou VPS |
| Sincronização interrompida a meio | limites de CPU/tempo do alojamento partilhado; baixar `--max-backfill` para 1 |
| Quota de disco cheia | a BD completa ocupa ~9 GB; ver *Disk Usage* |

Limites de recursos medidos: ingestão ~170 MB de RAM; recálculo do score ~100 MB de RAM, ~10 min de CPU.
