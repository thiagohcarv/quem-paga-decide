#!/usr/bin/env python3
"""Robô do painel Quem Paga Decide.

Baixa os dados oficiais da Câmara, do Senado e do TSE, guarda uma cópia em
docs/dados/ e monta o arquivo que o site lê. Se uma fonte falhar, a cópia
anterior daquela fonte é mantida (o site nunca fica vazio por causa de um
portal fora do ar) e a falha fica registrada em docs/dados/status.json.

Só usa a biblioteca padrão do Python.
"""
import csv
import datetime
import gzip
import http.client
import io
import json
import re
import sys
import time
import unicodedata
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "docs" / "dados"

LEGISLATURA = 57                  # 2023-2027
ANOS = [2023, 2024, 2025, 2026]   # "os últimos 4 anos"
ANO_ELEICAO = 2026
TIPOS_LEI = {"PL", "PLP", "PEC"}  # projetos de lei e emendas à Constituição
MAX_RECENTES = 12

CAMARA = "https://dadosabertos.camara.leg.br"
SENADO = "https://legis.senado.leg.br/dadosabertos"
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele"
ANOS_PATRIMONIO = [2018, 2022, 2026]  # eleições gerais com declaração de bens


# ---------------------------------------------------------------- utilidades

def baixar(url, binario=False, tentativas=3):
    """Baixa uma URL. Devolve JSON já lido, ou bytes se binario=True.

    Pede o arquivo comprimido (gzip) e, se a conexão cair no meio de um
    arquivo grande, retoma do ponto onde parou em vez de recomeçar.
    """
    erro, partes, comprimido = None, [], False
    for i in range(tentativas):
        cabecalhos = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "painel-quem-paga-decide/1.0 (dados abertos)",
        }
        recebido = sum(len(p) for p in partes)
        if recebido and not comprimido:
            cabecalhos["Range"] = f"bytes={recebido}-"
        else:
            partes = []
        try:
            with urlopen(Request(url, headers=cabecalhos), timeout=300) as resp:
                if resp.status != 206:  # servidor não retomou: começa do zero
                    partes = []
                comprimido = resp.headers.get("Content-Encoding", "").lower() == "gzip"
                while True:
                    bloco = resp.read(1 << 20)
                    if not bloco:
                        break
                    partes.append(bloco)
            corpo = b"".join(partes)
            if comprimido:
                corpo = gzip.decompress(corpo)
            return corpo if binario else json.loads(corpo.decode("utf-8"))
        except Exception as e:  # rede, HTTP ou JSON inválido
            erro = e
            if not isinstance(e, http.client.IncompleteRead) or comprimido:
                partes = []
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"falha ao baixar {url}: {erro}")


def normalizar(nome):
    """Nome sem acento, em maiúsculas e sem espaços duplos, para comparar."""
    s = unicodedata.normalize("NFKD", str(nome or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def lista(x):
    """A API do Senado devolve objeto quando há um item só. Sempre lista."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def cortar(texto, n=180):
    t = re.sub(r"\s+", " ", str(texto or "")).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def virou_norma(situacao):
    s = normalizar(situacao)
    return "NORMA JURIDICA" in s or "TRANSFORMADA EM NORMA" in s


def resumo_producao(itens):
    """itens: lista de dicts {id,tipo,numero,ano,ementa,situacao,data,url,principal}."""
    vistos, unicos = set(), []
    for it in itens:
        if it["id"] in vistos:
            continue
        vistos.add(it["id"])
        unicos.append(it)
    projetos = [i for i in unicos if i["tipo"] in TIPOS_LEI]
    por_tipo = {}
    for i in unicos:
        por_tipo[i["tipo"]] = por_tipo.get(i["tipo"], 0) + 1
    projetos.sort(key=lambda i: i.get("data") or "", reverse=True)
    return {
        "proposicoes": len(unicos),
        "projetos": len(projetos),
        "projetos_como_autor_principal": sum(1 for i in projetos if i.get("principal")),
        "viraram_norma": sum(1 for i in projetos if virou_norma(i.get("situacao"))),
        "por_tipo": dict(sorted(por_tipo.items(), key=lambda kv: -kv[1])),
        "recentes": [
            {k: i.get(k) for k in ("tipo", "numero", "ano", "ementa", "situacao", "data", "url")}
            for i in projetos[:MAX_RECENTES]
        ],
    }


# -------------------------------------------------------------------- Câmara

def etapa_camara():
    # 1) todos os deputados que exerceram mandato na legislatura
    deputados = {}
    for pagina in range(1, 40):
        r = baixar(f"{CAMARA}/api/v2/deputados?idLegislatura={LEGISLATURA}"
                   f"&itens=100&pagina={pagina}&ordem=ASC&ordenarPor=nome")
        dados = r.get("dados") or []
        if not dados:
            break
        for d in dados:
            deputados[int(d["id"])] = d
    if not deputados:
        raise RuntimeError("a Câmara devolveu a lista de deputados vazia")

    # 2) quem está em exercício hoje
    em_exercicio = set()
    try:
        for pagina in range(1, 12):
            r = baixar(f"{CAMARA}/api/v2/deputados?itens=100&pagina={pagina}")
            dados = r.get("dados") or []
            if not dados:
                break
            em_exercicio.update(int(d["id"]) for d in dados)
    except Exception as e:
        print("aviso: lista de deputados em exercício indisponível:", e)

    # 3) nome civil (para cruzar com o TSE)
    nome_civil, primeira_leg, nascimento, falecimento = {}, {}, {}, {}
    try:
        r = baixar(f"{CAMARA}/arquivos/deputados/json/deputados.json")
        for d in r.get("dados") or []:
            m = re.search(r"(\d+)$", str(d.get("uri") or ""))
            if m:
                nome_civil[int(m.group(1))] = d.get("nomeCivil") or ""
                nascimento[int(m.group(1))] = d.get("dataNascimento") or ""
                falecimento[int(m.group(1))] = d.get("dataFalecimento") or ""
                try:
                    primeira_leg[int(m.group(1))] = int(d.get("idLegislaturaInicial"))
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        print("aviso: nomes civis indisponíveis:", e)

    # 4) proposições e autores, ano a ano
    producao = {i: [] for i in deputados}
    for ano in ANOS:
        props = {}
        r = baixar(f"{CAMARA}/arquivos/proposicoes/json/proposicoes-{ano}.json")
        for p in r.get("dados") or []:
            st = p.get("ultimoStatus") or {}
            props[int(p["id"])] = {
                "id": f"cd-{p['id']}",
                "tipo": p.get("siglaTipo") or "",
                "numero": p.get("numero"),
                "ano": p.get("ano"),
                "ementa": cortar(p.get("ementa")),
                "situacao": st.get("descricaoSituacao")
                            or p.get("ultimoStatus_descricaoSituacao") or "",
                "data": str(p.get("dataApresentacao") or "")[:10],
                "url": "https://www.camara.leg.br/proposicoesWeb/"
                       f"fichadetramitacao?idProposicao={p['id']}",
            }
        del r
        r = baixar(f"{CAMARA}/arquivos/proposicoesAutores/json/proposicoesAutores-{ano}.json")
        for a in r.get("dados") or []:
            try:
                dep = int(a.get("idDeputadoAutor") or 0)
                prop = int(a.get("idProposicao") or 0)
            except (TypeError, ValueError):
                continue
            if dep in producao and prop in props:
                item = dict(props[prop])
                item["principal"] = (str(a.get("ordemAssinatura")) == "1"
                                     and str(a.get("proponente")) == "1")
                producao[dep].append(item)
        del r
        print(f"câmara {ano}: {len(props)} proposições lidas")

    saida = []
    for i, d in deputados.items():
        saida.append({
            "id": f"cd-{i}",
            "casa": "Câmara",
            "nome": d.get("nome") or "",
            "nome_civil": nome_civil.get(i, ""),
            "partido": d.get("siglaPartido") or "",
            "uf": d.get("siglaUf") or "",
            "foto": d.get("urlFoto") or "",
            "em_exercicio": i in em_exercicio,
            "pagina": f"https://www.camara.leg.br/deputados/{i}",
            # ano de início da primeira legislatura em que exerceu mandato
            "desde": (2023 - 4 * (LEGISLATURA - primeira_leg[i])) if i in primeira_leg else None,
            "nascimento": nascimento.get(i, ""),  # confirma o cruzamento com o TSE
            "falecimento": falecimento.get(i, ""),
            "producao": resumo_producao(producao[i]),
        })
    return saida


# -------------------------------------------------------------------- Senado

def achar(obj, chave):
    """Procura a primeira ocorrência de uma chave em um JSON aninhado."""
    if isinstance(obj, dict):
        if chave in obj:
            return obj[chave]
        for v in obj.values():
            r = achar(v, chave)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = achar(v, chave)
            if r is not None:
                return r
    return None


INICIO_LEG = "2023-02-01"


def autores_da_materia(texto):
    """'Senador Fulano (PT/SP), Líder do PL Beltrano (PL/RJ)' -> ['SENADOR FULANO', 'LIDER DO PL BELTRANO'].

    Só entram autores pessoas (com partido/UF entre parênteses); "Câmara dos
    Deputados" ou "Presidência da República" ficam de fora.
    """
    return [normalizar(n) for n in re.findall(r"([^,()]+?)\s*\([^()/]+/[A-Z]{2}\)", str(texto or ""))]


def e_o_mesmo(autor, alvo):
    return autor == alvo or autor.endswith(" " + alvo)


def senado_autorias(codigo, nome):
    """Matérias do senador desde 2023 pela rota /processo.

    A rota também devolve documentos ligados sem nenhum parlamentar na
    autoria (substitutivo da Câmara, veto da Presidência); esses ficam de fora.
    A lista de assinaturas às vezes vem cortada ("e outros"), então vale a
    coautoria informada pela própria rota. Autor principal: o primeiro da lista.
    """
    r = baixar(f"{SENADO}/processo?codigoParlamentarAutor={codigo}"
               f"&dataInicioApresentacao={INICIO_LEG}", tentativas=2)
    if not isinstance(r, list):
        raise RuntimeError("a rota /processo não devolveu uma lista")
    alvo = normalizar(nome)
    itens = []
    for p in r:
        if not isinstance(p, dict):
            continue
        autores = autores_da_materia(p.get("autoria"))
        if not autores:
            continue
        m = re.match(r"\s*([A-Za-z.]+)\s+(\d+)\s*/\s*(\d{4})", str(p.get("identificacao") or ""))
        if not m:
            continue
        cod = p.get("codigoMateria")
        situacao = str(p.get("situacaoAtual") or "")
        if p.get("normaGerada") and not virou_norma(situacao):
            situacao = f"Transformada em norma jurídica ({p['normaGerada']})"
        itens.append({
            "id": f"sf-{cod}",
            "tipo": m.group(1).upper(),
            "numero": int(m.group(2)),
            "ano": int(m.group(3)),
            "ementa": cortar(p.get("ementa")),
            "situacao": situacao.capitalize(),
            "data": str(p.get("dataApresentacao") or "")[:10],
            "url": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{cod}",
            "principal": e_o_mesmo(autores[0], alvo),
        })
    return itens


def exerceu_na_legislatura(mandato):
    """Algum período de exercício aberto ou encerrado depois de 1º/2/2023."""
    for e in lista((mandato.get("Exercicios") or {}).get("Exercicio")):
        if not e.get("DataFim") or e["DataFim"] >= INICIO_LEG:
            return True
    return False


def etapa_senado():
    # A lista da legislatura inclui suplentes que nunca assumiram; só entra
    # quem teve exercício a partir de 2023.
    r = baixar(f"{SENADO}/senador/lista/legislatura/{LEGISLATURA}.json")
    parlamentares = lista(achar(r, "Parlamentar"))
    if not parlamentares:
        raise RuntimeError("o Senado devolveu a lista de senadores vazia")

    atuais = {}
    try:
        ra = baixar(f"{SENADO}/senador/lista/atual.json")
        for p in lista(achar(ra, "Parlamentar")):
            ident = p.get("IdentificacaoParlamentar") or {}
            atuais[str(ident.get("CodigoParlamentar"))] = ident
    except Exception as e:
        print("aviso: lista de senadores em exercício indisponível:", e)

    saida, falhas = [], 0
    for p in parlamentares:
        ident = p.get("IdentificacaoParlamentar") or {}
        cod = str(ident.get("CodigoParlamentar") or "")
        mandatos = [m for m in lista((p.get("Mandatos") or {}).get("Mandato"))
                    if exerceu_na_legislatura(m)]
        if not cod or not mandatos:
            continue
        mandato = max(mandatos, key=lambda m: (m.get("PrimeiraLegislaturaDoMandato") or {}).get("DataInicio") or "")
        if str(mandato.get("DescricaoParticipacao") or "").startswith("Titular"):
            inicio = (mandato.get("PrimeiraLegislaturaDoMandato") or {}).get("DataInicio")
        else:  # suplente: desde quando assumiu pela primeira vez neste mandato
            inicio = min((e.get("DataInicio") or "9999")
                         for e in lista((mandato.get("Exercicios") or {}).get("Exercicio")))
        try:
            desde = int(str(inicio)[:4])
        except (TypeError, ValueError):
            desde = None
        atual = atuais.get(cod) or {}
        nome = ident.get("NomeParlamentar") or ""
        try:
            basicos = achar(baixar(f"{SENADO}/senador/{cod}.json", tentativas=2), "DadosBasicosParlamentar") or {}
        except Exception as e:
            print(f"aviso: senador {cod} ({nome}), detalhe: {e}")
            basicos = {}
        fim = (mandato.get("SegundaLegislaturaDoMandato") or {}).get("DataFim") or ""
        try:
            itens = senado_autorias(cod, nome)
        except Exception as e:
            print(f"aviso: senador {cod} ({nome}): {e}")
            itens = None
            falhas += 1
        saida.append({
            "id": f"sf-{cod}",
            "casa": "Senado",
            "nome": nome,
            "nome_civil": ident.get("NomeCompletoParlamentar") or "",
            "partido": atual.get("SiglaPartidoParlamentar") or ident.get("SiglaPartidoParlamentar") or "",
            "uf": mandato.get("UfParlamentar") or ident.get("UfParlamentar") or "",
            "foto": ident.get("UrlFotoParlamentar") or atual.get("UrlFotoParlamentar") or "",
            "em_exercicio": cod in atuais,
            "pagina": f"https://www25.senado.leg.br/web/senadores/senador/-/perfil/{cod}",
            "desde": desde,  # titular: início do mandato atual; suplente: quando assumiu
            "mandato_ate": int(fim[:4]) if fim[:4].isdigit() else None,
            "nascimento": basicos.get("DataNascimento") or "",
            "falecimento": basicos.get("DataFalecimento") or "",
            "producao": resumo_producao(itens) if itens is not None else None,
        })
        time.sleep(0.3)
    if falhas == len(saida):
        raise RuntimeError("a rota de autorias do Senado não respondeu")
    return saida


# ----------------------------------------------------------------------- TSE

def csvs_do_zip(url):
    """Abre um zip do TSE e devolve, linha a linha, o CSV nacional.

    O zip traz _BRASIL.csv (todos os estados), _BR.csv (só presidente) e um
    arquivo por estado. Ler mais de um somaria bens em dobro.
    """
    bruto = baixar(url, binario=True)
    with zipfile.ZipFile(io.BytesIO(bruto)) as z:
        nomes = [n for n in z.namelist() if n.lower().endswith(".csv")]
        brasil = [n for n in nomes if n.upper().endswith("_BRASIL.CSV")]
        estados = [n for n in nomes if not n.upper().endswith("_BR.CSV")]
        for nome in (brasil or estados):
            with z.open(nome) as f:
                texto = io.TextIOWrapper(f, encoding="latin-1", newline="")
                yield from csv.DictReader(texto, delimiter=";")


CARGOS = {
    "DEPUTADO FEDERAL": "Deputado federal", "SENADOR": "Senador",
    "1O SUPLENTE": "1º suplente de senador", "2O SUPLENTE": "2º suplente de senador",
    "PRESIDENTE": "Presidente", "VICE-PRESIDENTE": "Vice-presidente",
    "GOVERNADOR": "Governador", "VICE-GOVERNADOR": "Vice-governador",
    "DEPUTADO ESTADUAL": "Deputado estadual", "DEPUTADO DISTRITAL": "Deputado distrital",
}
FEDERAIS = {"Deputado federal", "Senador"}


def data_iso(texto):
    """'27/10/1945' -> '1945-10-27'. Vazio se não for uma data."""
    m = re.match(r"\s*(\d{2})/(\d{2})/(\d{4})", str(texto or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def resultado(situacao):
    """Situação do TSE -> eleito, segundo_turno, nao_eleito ou indefinido."""
    s = normalizar(situacao)
    if s.startswith("ELEITO"):
        return "eleito"
    if "TURNO" in s:
        return "segundo_turno"
    if s in ("NAO ELEITO", "SUPLENTE"):
        return "nao_eleito"
    return "indefinido"  # "#NULO" e vazio: o arquivo não diz o desfecho


def tse_candidatos(ano):
    """Candidatos de todos os cargos naquele ano (eleitos ou não)."""
    cands, vistos = [], set()
    for linha in csvs_do_zip(f"{TSE}/consulta_cand/consulta_cand_{ano}.zip"):
        bruto = normalizar(linha.get("DS_CARGO"))
        cargo = CARGOS.get(bruto) or bruto.capitalize()
        sq = linha.get("SQ_CANDIDATO") or ""
        if not sq or sq in vistos:
            continue
        vistos.add(sq)
        cands.append({
            "sq": sq,
            "nome": (linha.get("NM_CANDIDATO") or "").title(),
            "nome_urna": (linha.get("NM_URNA_CANDIDATO") or "").title(),
            "partido": linha.get("SG_PARTIDO") or "",
            "uf": linha.get("SG_UF") or "",
            "cargo": cargo,
            "situacao": normalizar(linha.get("DS_SIT_TOT_TURNO")),
            "ocupacao": (linha.get("DS_OCUPACAO") or "").capitalize(),
            "nascimento": data_iso(linha.get("DT_NASCIMENTO")),
        })
    if not any(c["cargo"] in FEDERAIS for c in cands):
        raise RuntimeError(f"arquivo de candidatos de {ano} veio sem deputados e senadores")
    return cands


def tse_bens(ano, sqs):
    """Soma dos bens declarados por candidato. Devolve {sq: valor em reais}."""
    total = {}
    for linha in csvs_do_zip(f"{TSE}/bem_candidato/bem_candidato_{ano}.zip"):
        sq = linha.get("SQ_CANDIDATO") or ""
        if sq not in sqs:
            continue
        try:
            valor = float(str(linha.get("VR_BEM_CANDIDATO") or "0").replace(".", "").replace(",", "."))
        except ValueError:
            continue
        total[sq] = total.get(sq, 0.0) + valor
    return total


def etapa_tse():
    """Eleitos em 2026 e patrimônio declarado em cada eleição.

    Cada ano é independente: se um falhar, vale a cópia anterior daquele ano.
    """
    copia = ler("copia_tse.json", {})
    if not isinstance(copia, dict):
        copia = {}
    saida = {"eleitos": copia.get("eleitos") or [],
             "candidaturas_2026": copia.get("candidaturas_2026") or [],
             "resultado_2026": bool(copia.get("resultado_2026")),
             "patrimonio": dict(copia.get("patrimonio") or {}),
             "avisos": []}
    algum = False
    for ano in ANOS_PATRIMONIO:
        try:
            todos = tse_candidatos(ano)
        except Exception as e:
            saida["avisos"].append(f"candidatos {ano}: {cortar(e, 120)}")
            continue
        algum = True
        cands = [c for c in todos if c["cargo"] in FEDERAIS]
        if ano == ANO_ELEICAO:
            # Todos os cargos de 2026, para os selos de quem tinha mandato.
            saida["candidaturas_2026"] = [
                {k: c[k] for k in ("sq", "nome", "nome_urna", "uf", "cargo", "situacao", "nascimento")}
                for c in todos]
            saida["resultado_2026"] = any(resultado(c["situacao"]) == "eleito" for c in cands)
            eleitos = [c for c in cands if resultado(c["situacao"]) == "eleito"]
            if eleitos:
                saida["eleitos"] = eleitos
            else:
                saida["avisos"].append(f"o TSE ainda não marcou os eleitos de {ano}")
        try:
            bens = tse_bens(ano, {c["sq"] for c in cands})
        except Exception as e:
            saida["avisos"].append(f"bens {ano}: {cortar(e, 120)}")
            continue
        # Chaves "C|nome civil|UF" e "U|nome de urna|UF". Se a mesma chave
        # aponta para pessoas com datas de nascimento diferentes (homônimos),
        # ela é descartada: melhor sem patrimônio do que com o valor de outro.
        donos, por_pessoa = {}, {}
        for c in cands:
            for tipo, nome in (("C", c["nome"]), ("U", c["nome_urna"])):
                if nome:
                    donos.setdefault(f"{tipo}|{normalizar(nome)}|{c['uf']}", set()).add(c["nascimento"])
        for c in cands:
            # quem não declarou bens fica fora (não é o mesmo que patrimônio zero)
            if c["sq"] not in bens:
                continue
            for tipo, nome in (("C", c["nome"]), ("U", c["nome_urna"])):
                chave = f"{tipo}|{normalizar(nome)}|{c['uf']}"
                if nome and len(donos[chave]) == 1:
                    por_pessoa.setdefault(chave, round(bens[c["sq"]], 2))
        ambiguas = sum(1 for v in donos.values() if len(v) > 1)
        if ambiguas:
            print(f"tse {ano}: {ambiguas} nomes com homônimos no mesmo estado, sem patrimônio")
        saida["patrimonio"][str(ano)] = por_pessoa
        print(f"tse {ano}: {len(cands)} candidatos, {len(bens)} com bens declarados")
    if not algum:
        raise RuntimeError("; ".join(saida["avisos"]) or "TSE indisponível")
    return saida


# ------------------------------------------------------------------- junção

POUCO_INFORMATIVOS = {"DE", "DA", "DO", "DOS", "DAS", "E", "JUNIOR", "FILHO", "NETO", "SOBRINHO"}


def candidatura_2026(p, por_nascimento, por_nome):
    """Acha a candidatura de 2026 de quem teve mandato.

    Devolve (candidatura ou None, incerto, registros achados). Exige a mesma
    data de nascimento do Congresso, o que permite achar quem concorreu por
    outro estado sem confundir homônimos. Incerto = não dá para afirmar se
    concorreu ou não.
    """
    nasc = str(p.get("nascimento") or "")[:10]
    if not nasc:
        return None, True, []
    civil, parl = normalizar(p.get("nome_civil")), normalizar(p.get("nome"))
    mesma_data = por_nascimento.get(nasc, [])
    achadas = [c for c in mesma_data
               if (civil and normalizar(c["nome"]) == civil) or (parl and normalizar(c["nome_urna"]) == parl)]
    if not achadas:  # nome escrito de outro jeito: mesma data, mesmo estado e 2+ nomes em comum
        tokens = set(civil.split()) - POUCO_INFORMATIVOS
        achadas = [c for c in mesma_data if c["uf"] in (p["uf"], "BR")
                   and len(tokens & set(normalizar(c["nome"]).split())) >= 2]
    # Mais de um registro da mesma pessoa (ex.: candidatura anulada e trocada
    # por outra): vale o único com desfecho informado.
    com_desfecho = [c for c in achadas if resultado(c["situacao"]) != "indefinido"]
    if len(achadas) > 1 and len(com_desfecho) == 1:
        return com_desfecho[0], False, achadas
    if len(achadas) == 1:
        return achadas[0], False, achadas
    if achadas:
        return None, True, achadas
    # Mesmo nome e estado, mas data diferente: pode ser erro de cadastro.
    if f"C|{civil}|{p['uf']}" in por_nome or f"U|{parl}|{p['uf']}" in por_nome:
        return None, True, []
    return None, False, []


def juntar(camara, senado, tse):
    tse = tse or {}
    patrimonio = tse.get("patrimonio") or {}
    candidaturas = tse.get("candidaturas_2026") or []
    marcado = bool(tse.get("resultado_2026"))
    pessoas = list(camara) + list(senado)

    por_nascimento, por_nome = {}, set()
    for c in candidaturas:
        por_nascimento.setdefault(c["nascimento"], []).append(c)
        por_nome.add(f"C|{normalizar(c['nome'])}|{c['uf']}")
        por_nome.add(f"U|{normalizar(c['nome_urna'])}|{c['uf']}")

    # Selos de 2026 (só depois que o TSE marcou o resultado):
    #   demitido       tinha mandato, disputou qualquer cargo e não se elegeu
    #   segundo_turno  tinha mandato e ainda disputa o 2º turno
    #   eleito         tinha mandato e se elegeu (cargo em eleito_2026)
    #   nao_concorreu  tinha mandato e não aparece entre os candidatos
    #   experiencia    eleito deputado federal ou senador sem mandato em 2023-2026
    # Sem selo: falecidos, cruzamento incerto, desfecho não informado pelo TSE
    # e senador com mandato até 2031 que não estava em disputa.
    usadas, nomes_congresso = set(), set()
    for p in pessoas:
        p["eleito_2026"], p["selo_2026"], p["cargo_2026"] = None, None, None
        for tipo, nome in (("C", p.get("nome_civil")), ("U", p.get("nome"))):
            if nome:
                nomes_congresso.add(f"{tipo}|{normalizar(nome)}|{p['uf']}")
        cand, incerto, achadas = candidatura_2026(p, por_nascimento, por_nome)
        usadas.update(c["sq"] for c in achadas)
        if not marcado or incerto or p.get("falecimento"):
            continue
        fica_ate_2031 = p["casa"] == "Senado" and (p.get("mandato_ate") or 0) >= 2031
        if cand is None:
            if not fica_ate_2031:
                p["selo_2026"] = "nao_concorreu"
            continue
        r = resultado(cand["situacao"])
        if r == "eleito":
            p["eleito_2026"] = cand["cargo"]
            p["selo_2026"] = "eleito"
        elif r == "segundo_turno":
            p["selo_2026"], p["cargo_2026"] = "segundo_turno", cand["cargo"]
        elif r == "nao_eleito" and not fica_ate_2031:
            p["selo_2026"], p["cargo_2026"] = "demitido", cand["cargo"]
        elif r == "nao_eleito":
            # perdeu outra disputa, mas o mandato no Senado continua até 2031
            p["cargo_2026"] = cand["cargo"]

    novos = []
    for e in candidaturas:
        if e["cargo"] not in FEDERAIS or resultado(e["situacao"]) != "eleito" or e["sq"] in usadas:
            continue
        # mesmo nome e estado de alguém do Congresso sem confirmação: sem selo
        duvida = (f"C|{normalizar(e['nome'])}|{e['uf']}" in nomes_congresso
                  or f"U|{normalizar(e['nome_urna'])}|{e['uf']}" in nomes_congresso)
        extra = next((x for x in tse.get("eleitos") or [] if x.get("sq") == e["sq"]), {})
        novos.append({
            "id": f"tse-{e['sq']}",
            "casa": None,
            "nome": e["nome_urna"] or e["nome"],
            "nome_civil": e["nome"],
            "partido": extra.get("partido") or "",
            "uf": e["uf"],
            "foto": "",
            "em_exercicio": False,
            "pagina": "",
            "desde": None,
            "eleito_2026": e["cargo"],
            "selo_2026": None if duvida else "experiencia",
            "cargo_2026": None,
            "ocupacao_declarada": extra.get("ocupacao") or "",
            # Sem mandato no Congresso em 2023-2026: não há base única com o
            # histórico de vereadores, deputados estaduais e prefeitos.
            "producao": None,
        })
    todos = pessoas + novos
    for p in todos:
        p["chave"] = f"{normalizar(p.get('nome_civil') or p['nome'])}|{p['uf']}"
        p["patrimonio"] = {}
        for ano, mapa in patrimonio.items():
            for tipo, nome in (("C", p.get("nome_civil")), ("U", p.get("nome"))):
                valor = mapa.get(f"{tipo}|{normalizar(nome)}|{p['uf']}") if nome else None
                if valor is not None:
                    p["patrimonio"][ano] = valor
                    break
    todos.sort(key=lambda p: normalizar(p["nome"]))
    return todos


# --------------------------------------------------------------------- main

def ler(arquivo, padrao):
    try:
        return json.loads((DADOS / arquivo).read_text(encoding="utf-8"))
    except Exception:
        return padrao


def gravar(arquivo, conteudo):
    DADOS.mkdir(parents=True, exist_ok=True)
    (DADOS / arquivo).write_text(
        json.dumps(conteudo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    agora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    anterior = ler("status.json", {})
    status = {"ultima_execucao": agora, "fontes": {}}
    resultado = {}

    etapas = (
        ("camara", "Câmara dos Deputados", etapa_camara, "copia_camara.json"),
        ("senado", "Senado Federal", etapa_senado, "copia_senado.json"),
        ("tse", "TSE (eleitos e patrimônio declarado)", etapa_tse, "copia_tse.json"),
    )
    for chave, rotulo, funcao, arquivo in etapas:
        antes = (anterior.get("fontes") or {}).get(chave) or {}
        try:
            dados = funcao()
            gravar(arquivo, dados)
            status["fontes"][chave] = {
                "nome": rotulo, "ok": True,
                "registros": len(dados.get("eleitos") or []) if isinstance(dados, dict) else len(dados),
                "dados_de": agora,
                "mensagem": "; ".join(dados.get("avisos") or []) if isinstance(dados, dict) else "",
            }
            print(f"{rotulo}: {status['fontes'][chave]['registros']} registros")
        except Exception as e:
            dados = ler(arquivo, {} if chave == "tse" else [])
            status["fontes"][chave] = {
                "nome": rotulo, "ok": False, "registros": len(dados),
                "dados_de": antes.get("dados_de"),
                "mensagem": cortar(e, 300),
            }
            print(f"FALHA em {rotulo}: {e}\n  usando a cópia anterior ({len(dados)} registros)")
        resultado[chave] = dados

    todos = juntar(resultado["camara"], resultado["senado"], resultado["tse"])
    gravar("parlamentares.json", todos)
    status["total"] = len(todos)
    gravar("status.json", status)
    if not (DADOS / "processos.json").exists():
        gravar("processos.json", [])
    print(f"total no painel: {len(todos)}")

    # Só reprova a execução se nada funcionou e não havia cópia nenhuma.
    if not todos:
        sys.exit("nenhuma fonte respondeu e não há cópia anterior")


if __name__ == "__main__":
    main()
