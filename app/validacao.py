import math
import re
import time
import unicodedata
from difflib import SequenceMatcher

from app.spotify_api import requisitar


def normalizar_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    # Remove também parênteses aninhados, sem apagar o restante do título.
    while re.search(r"\([^()]*\)", texto):
        texto = re.sub(r"\([^()]*\)", " ", texto)
    texto = re.sub(r"\[[^\]]*\]", " ", texto)
    texto = re.sub(r"\s*[-–—]\s*(?:(?:\d{4}\s+)?remaster(?:ed|izado)?\b|ao vivo\b|live\b).*$", "", texto)
    texto = re.sub(r"\bao\s+vivo\b", " ", texto)
    texto = re.sub(r"[^\w\s]|_", " ", texto)
    return " ".join(texto.split())


def similaridade(texto_a: str, texto_b: str) -> float:
    a, b = normalizar_texto(texto_a), normalizar_texto(texto_b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def melhor_correspondencia(sugestao: dict, resultados: list, limiar: float = 0.85):
    """Exige o limiar em ambos os campos e escolhe a maior média."""
    if not math.isfinite(limiar) or not 0 < limiar <= 1:
        raise ValueError("O limiar deve estar entre 0 (exclusivo) e 1.")
    melhor, melhor_nota = None, -1
    for faixa in resultados:
        if not faixa.get("uri", "").startswith("spotify:track:"):
            continue
        titulo = similaridade(sugestao["titulo"], faixa.get("name", ""))
        artista = max((similaridade(sugestao["artista"], a.get("name", ""))
                       for a in faixa.get("artists", [])), default=0.0)
        nota = (titulo + artista) / 2
        if titulo >= limiar and artista >= limiar and nota > melhor_nota:
            melhor, melhor_nota = faixa, nota
    return melhor


def validar_musicas(sugestoes: list[dict], token: str, limiar: float = 0.85,
                    pausa: float = 0.2) -> tuple[list[dict], list[dict]]:
    """Retorna (validadas, descartadas); erros da API interrompem a validação.

    Validadas preservam a sugestão e acrescentam uri. Descartadas acrescentam
    motivo. O ano sugerido não é verificado. Nenhum dado é enviado à IA.
    """
    if not math.isfinite(limiar) or not 0 < limiar <= 1:
        raise ValueError("O limiar deve estar entre 0 (exclusivo) e 1.")
    if not math.isfinite(pausa) or pausa < 0:
        raise ValueError("A pausa deve ser um número não negativo.")
    validadas, descartadas = [], []
    uris, chaves, entradas = set(), set(), set()
    buscou = False

    def buscar(query):
        nonlocal buscou
        if buscou:
            time.sleep(pausa)
        buscou = True
        resposta = requisitar(
            "GET", "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "type": "track", "limit": 5},
        )
        resposta.raise_for_status()
        return resposta.json()["tracks"]["items"]

    for sugestao in sugestoes:
        titulo, artista = sugestao["titulo"], sugestao["artista"]
        chave_entrada = (normalizar_texto(titulo), normalizar_texto(artista))
        motivo = None
        if not all(chave_entrada):
            motivo = "Título ou artista vazio após normalização."
        elif chave_entrada in entradas:
            motivo = "Sugestão duplicada."
        else:
            entradas.add(chave_entrada)
            resultados = buscar(f"track:{titulo} artist:{artista}")
            if not resultados:
                resultados = buscar(f"{titulo} {artista}")
            melhor = melhor_correspondencia(sugestao, resultados, limiar)
            if not resultados:
                motivo = "Nenhum resultado encontrado no Spotify."
            elif melhor is None:
                motivo = "Nenhum resultado com título e artista suficientemente parecidos."
            else:
                chave = (normalizar_texto(melhor["name"]), tuple(sorted(
                    normalizar_texto(a["name"]) for a in melhor["artists"])))
                if melhor["uri"] in uris or chave in chaves:
                    motivo = "Música duplicada no Spotify."
                else:
                    uris.add(melhor["uri"])
                    chaves.add(chave)
                    validadas.append({**sugestao, "uri": melhor["uri"]})
        if motivo:
            descartadas.append({**sugestao, "motivo": motivo})
    return validadas, descartadas
