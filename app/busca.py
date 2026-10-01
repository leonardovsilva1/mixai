import re
import time
from difflib import SequenceMatcher
from collections import Counter

from app.spotify_api import requisitar
from app.validacao import normalizar_texto


def resolver_artista(nome, token):
    """Encontra a grafia principal do artista antes de buscar suas faixas."""
    resposta = requisitar(
        "GET", "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": nome, "type": "artist", "limit": 10},
    )
    resposta.raise_for_status()
    alvo = normalizar_texto(nome)
    for artista in resposta.json().get("artists", {}).get("items", []):
        candidato = artista.get("name", "")
        if candidato and SequenceMatcher(None, alvo, normalizar_texto(candidato)).ratio() >= 0.84:
            return candidato
    return nome


def pesquisar_musicas(plano, token, quantidade=20, paginas=3, limite_por_artista=3,
                     permitir_genero_livre=False):
    """Pesquisa até 10 faixas por página; seleciona localmente sem chamar IA."""
    if type(quantidade) is not int or not 1 <= quantidade <= 100:
        raise ValueError("A quantidade deve estar entre 1 e 100.")
    if type(paginas) is not int or not 1 <= paginas <= 10:
        raise ValueError("O número de páginas deve estar entre 1 e 10.")
    consultas = []
    termos_mood = " ".join(plano.get("termos_mood", []))
    artistas_pedidos = plano.get("artistas_pedidos", [])
    artistas_ordenados = list(dict.fromkeys([*artistas_pedidos, *plano["artistas"]]))
    for campo, valores in (("artist", artistas_ordenados), ("genre", plano["generos"])):
        for valor in valores:
            limpo = re.sub(r'["\\:\r\n]', ' ', valor).strip()
            query = f'{campo}:"{limpo}"'
            if campo == "artist" and termos_mood:
                query += f" {termos_mood}"
            if plano["ano_inicio"] is not None:
                query += f' year:{plano["ano_inicio"]}-{plano["ano_fim"]}'
            consultas.append((query, valor if campo == "artist" else None))
    grupos = []
    referencias = {normalizar_texto(a) for a in artistas_ordenados}
    requisicoes = 0
    examinadas = 0
    indice_consulta = 0
    while indice_consulta < len(consultas):
        query, artista = consultas[indice_consulta]
        indice_consulta += 1
        grupo = []
        for pagina in range(paginas):
            if requisicoes:
                time.sleep(0.2)
            resposta = requisitar("GET", "https://api.spotify.com/v1/search",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "type": "track", "limit": 10, "offset": pagina * 10})
            requisicoes += 1
            resposta.raise_for_status()
            dados = resposta.json()["tracks"]
            for faixa in dados["items"]:
                examinadas += 1
                nomes = [a["name"] for a in faixa.get("artists", [])]
                if (not artista and referencias and not permitir_genero_livre
                    and not referencias.intersection(map(normalizar_texto, nomes))):
                    continue
                if artista and not corresponde_artista(artista, nomes):
                    continue
                data = faixa.get("album", {}).get("release_date", "")
                ano = int(data[:4]) if re.match(r"^\d{4}", data) else None
                if plano["ano_inicio"] is not None and (
                    ano is None or not plano["ano_inicio"] <= ano <= plano["ano_fim"]
                ):
                    continue
                if (not nomes or not faixa.get("name") or faixa.get("is_playable") is False
                    or not faixa.get("uri", "").startswith("spotify:track:")):
                    continue
                grupo.append({
                    "titulo": faixa["name"], "artista": ", ".join(nomes), "ano": ano,
                    "uri": faixa["uri"], "url": faixa.get("external_urls", {}).get("spotify", ""),
                    "_artistas": tuple(sorted(set(normalizar_texto(n) for n in nomes))),
                    "_isrc": faixa.get("external_ids", {}).get("isrc"),
                })
            if not dados["items"] or not dados.get("next"):
                break
        grupos.append(grupo)
        # O Spotify pesquisa texto indexado, não atributos de áudio: termos como
        # "808 pesado" podem zerar a consulta mesmo quando o artista existe.
        # Tenta o catálogo do artista apenas quando a busca conjunta não trouxe nada.
        if artista and termos_mood and termos_mood in query and not grupo:
            limpo = re.sub(r'["\\:\r\n]', ' ', artista).strip()
            consulta_artista = f'artist:"{limpo}"'
            if plano["ano_inicio"] is not None:
                consulta_artista += f' year:{plano["ano_inicio"]}-{plano["ano_fim"]}'
            consultas.append((consulta_artista, artista))
    # Intercala consultas para evitar que o primeiro artista ocupe toda a lista.
    selecionadas, uris, chaves, isrcs = [], set(), set(), set()
    contagem = Counter()
    for indice in range(max(map(len, grupos), default=0)):
        for grupo in grupos:
            if indice >= len(grupo):
                continue
            musica = grupo[indice]
            artistas = musica["_artistas"]
            chave = (normalizar_texto(musica["titulo"]), artistas)
            isrc = musica["_isrc"]
            if (musica["uri"] in uris or chave in chaves or (isrc and isrc in isrcs)
                or (limite_por_artista is not None and any(contagem[a] >= limite_por_artista for a in artistas))):
                continue
            uris.add(musica["uri"])
            chaves.add(chave)
            if isrc:
                isrcs.add(isrc)
            contagem.update(artistas)
            selecionadas.append({k: v for k, v in musica.items() if not k.startswith("_")})
            if len(selecionadas) == quantidade:
                return selecionadas, examinadas
    return selecionadas, examinadas


def corresponde_artista(pedido, nomes):
    alvo = normalizar_texto(pedido)
    for nome in nomes:
        nome_normalizado = normalizar_texto(nome)
        if alvo == nome_normalizado:
            return True
        # Tolera pequenos erros de grafia em nomes compridos, sem aproximar nomes curtos.
        if len(alvo) >= 8 and SequenceMatcher(None, alvo, nome_normalizado).ratio() >= 0.84:
            return True
    return False
