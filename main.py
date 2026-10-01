import argparse
import math
import sys
from collections import Counter

import requests

from app.ia import sugerir_musicas
from app.ia import planejar_busca
from app.busca import pesquisar_musicas, corresponde_artista, resolver_artista
from app.spotify_api import criar_playlist, adicionar_musicas, verificar_cota_spotify
from app.spotify_auth import obter_token_valido
from app.validacao import normalizar_texto, validar_musicas


def criar_playlist_por_mood(mood, tamanho=20, informar=None):
    musicas = selecionar_musicas_por_mood(mood, tamanho, informar=informar)
    return _publicar_playlist(mood, musicas)


def selecionar_musicas_por_mood(mood, tamanho=20, informar=None, exigir_quantidade=True,
                                reservas=0):
    if not isinstance(mood, str) or not mood.strip() or len(mood) > 300:
        raise ValueError("O mood deve ter de 1 a 300 caracteres.")
    if type(tamanho) is not int or tamanho <= 0:
        raise ValueError("O tamanho deve ser um inteiro positivo.")
    verificar_cota_spotify()
    token = obter_token_valido()
    try:
        plano = planejar_busca(mood)
        if plano["tipo"] == "referencia":
            selecionadas = _buscar_por_referencia(mood, tamanho, token, plano, informar, reservas)
            if len(selecionadas) < tamanho and exigir_quantidade:
                raise RuntimeError(
                    f"Encontrei apenas {len(selecionadas)} faixas parecidas com a referência; "
                    "nenhuma playlist foi criada."
                )
            return selecionadas
        if plano["tipo"] in {"artista", "mood_artista"}:
            plano_busca = dict(plano)
            if plano["tipo"] == "artista":
                artistas_spotify = [resolver_artista(a, token) for a in plano["artistas_pedidos"]]
                plano_busca["artistas"] = artistas_spotify
                plano_busca["artistas_pedidos"] = artistas_spotify
                plano_busca["generos"] = []
                plano_busca["termos_mood"] = []
                limite_por_artista = None
                selecionadas, _ = pesquisar_musicas(
                    plano_busca, token, quantidade=tamanho,
                    limite_por_artista=limite_por_artista,
                )
            else:
                selecionadas = _buscar_mood_com_artistas(mood, tamanho, token, plano, informar, reservas)
            if informar:
                informar("Buscando faixas no Spotify com base nos artistas e no mood…")
            else:
                print("Buscando faixas no Spotify com base nos artistas e no mood...", flush=True)
            if not selecionadas:
                raise RuntimeError("Não encontrei faixas que correspondam aos artistas e ao mood no Spotify.")
            if len(selecionadas) < tamanho and not informar:
                print(f"Foram encontradas {len(selecionadas)} de {tamanho} faixas; a playlist terá essa quantidade.")
            return selecionadas

        selecionadas, titulos_anteriores = [], []
        vistas, uris = set(), set()
        artistas = Counter()
        for rodada in range(2):
            faltam = tamanho - len(selecionadas)
            if faltam <= 0:
                break
            if informar:
                informar(f"Rodada {rodada + 1}/2: buscando sugestões e validando no Spotify…")
            else:
                print(f"Rodada {rodada + 1}/2: sugerindo e validando músicas...", flush=True)
            # Exclusivamente títulos produzidos pela IA, nunca dados do Spotify.
            sugestoes = sugerir_musicas(mood, math.ceil(faltam * 1.5),
                                       titulos_anteriores=list(titulos_anteriores))
            novas = []
            for sugestao in sugestoes:
                titulos_anteriores.append(sugestao["titulo"])
                chave = (normalizar_texto(sugestao["artista"]), normalizar_texto(sugestao["titulo"]))
                if chave not in vistas:
                    vistas.add(chave)
                    novas.append(sugestao)
            validas, _ = validar_musicas(novas, token)
            for musica in validas:
                artista = normalizar_texto(musica["artista"])
                if musica["uri"] in uris or artistas[artista] >= 3:
                    continue
                uris.add(musica["uri"])
                artistas[artista] += 1
                selecionadas.append(musica)
                if len(selecionadas) == tamanho + reservas:
                    break
        if not selecionadas:
            raise RuntimeError("Nenhuma música foi validada após duas tentativas. Nenhuma playlist foi criada.")
        if len(selecionadas) < tamanho and not informar:
            print(f"Foram validadas {len(selecionadas)} de {tamanho} músicas; a playlist terá essa quantidade.")
        return selecionadas
    finally:
        if informar:
            informar("Seleção pronta")


def _buscar_mood_com_artistas(mood, tamanho, token, plano, informar=None, reservas=0):
    artistas_pedidos = plano["artistas_pedidos"]
    artistas_spotify = [resolver_artista(a, token) for a in artistas_pedidos]
    if len(artistas_pedidos) > tamanho:
        raise ValueError("O tamanho da playlist precisa comportar todos os artistas pedidos.")

    selecionadas, sobras, uris, titulos_anteriores = [], [], set(), []
    limite_sugestoes = tamanho - max(0, len(artistas_pedidos) - 1)
    for rodada in range(2):
        faltam = limite_sugestoes - len(selecionadas)
        if faltam <= 0:
            break
        mensagem = f"Rodada {rodada + 1}/2: sugerindo faixas do mood e dos artistas pedidos…"
        if informar:
            informar(mensagem)
        else:
            print(mensagem, flush=True)
        sugestoes = sugerir_musicas(
            mood, math.ceil(faltam * 1.5),
            titulos_anteriores=list(titulos_anteriores),
            artistas_pedidos=artistas_pedidos,
        )
        candidatas = []
        for sugestao in sugestoes:
            titulos_anteriores.append(sugestao["titulo"])
            artista_pedido = next((oficial for pedido, oficial in zip(artistas_pedidos, artistas_spotify)
                if corresponde_artista(pedido, sugestao["artista"].split(", "))), None)
            if artista_pedido:
                candidatas.append({**sugestao, "artista": artista_pedido})
        validas, _ = validar_musicas(candidatas, token)
        for musica in validas:
            if musica["uri"] in uris:
                continue
            uris.add(musica["uri"])
            if len(selecionadas) < limite_sugestoes:
                selecionadas.append(musica)
            elif len(sobras) < reservas:
                sobras.append(musica)
            if len(selecionadas) >= limite_sugestoes and len(sobras) >= reservas:
                break

    ausentes = [artista for artista in artistas_spotify if not any(
        corresponde_artista(artista, musica["artista"].split(", ")) for musica in selecionadas
    )]
    plano_catalogo = dict(plano)
    plano_catalogo["artistas"] = ausentes or artistas_spotify
    plano_catalogo["artistas_pedidos"] = artistas_spotify
    faltam = tamanho - len(selecionadas)
    if faltam:
        if informar:
            informar("Completando a quantidade com faixas encontradas no Spotify…")
        extras, _ = pesquisar_musicas(
            plano_catalogo, token, quantidade=faltam, limite_por_artista=None,
        )
        for musica in extras:
            if musica["uri"] not in uris:
                uris.add(musica["uri"])
                selecionadas.append(musica)
                if len(selecionadas) == tamanho:
                    break

    ausentes = [artista for artista in artistas_spotify if not any(
        corresponde_artista(artista, musica["artista"].split(", ")) for musica in selecionadas
    )]
    if ausentes:
        raise RuntimeError(
            "Não encontrei faixas no Spotify para todos os artistas pedidos: "
            + ", ".join(ausentes)
        )
    return (selecionadas + sobras)[:tamanho + reservas]


def _buscar_por_referencia(mood, tamanho, token, plano, informar=None, reservas=0):
    referencia = plano["referencia"]
    selecionadas, uris, contagem = [], set(), Counter()
    titulos_anteriores = []

    def incluir(musica):
        if musica["uri"] in uris or len(selecionadas) >= tamanho + reservas:
            return
        nomes = musica["artista"].split(", ")
        principal = normalizar_texto(nomes[0])
        eh_referencia = corresponde_artista(referencia["artista"], nomes)
        if contagem[principal] >= 5 or (eh_referencia and contagem["_referencia"] >= 3):
            return
        uris.add(musica["uri"])
        contagem[principal] += 1
        if eh_referencia:
            contagem["_referencia"] += 1
        selecionadas.append(musica)

    # O título veio do usuário; só é incluído se o Spotify confirmar a gravação.
    original, _ = validar_musicas([{
        "titulo": referencia["titulo"], "artista": referencia["artista"],
    }], token)
    for musica in original:
        incluir(musica)

    contexto = {
        "generos": plano["generos"][:1], "termos_mood": plano["termos_mood"],
        "artistas_proximos": plano["artistas"],
    }
    for rodada in range(2):
        faltam = tamanho - len(selecionadas)
        if not faltam:
            break
        if informar:
            informar(f"Rodada {rodada + 1}/2: buscando músicas parecidas com a referência…")
        try:
            sugestoes = sugerir_musicas(
                mood, math.ceil(faltam * 1.5),
                titulos_anteriores=list(titulos_anteriores),
                referencia=referencia, contexto_referencia=contexto,
            )
        except RuntimeError as erro:
            if not str(erro).startswith(("O Gemini está indisponível", "Falha de rede ao acessar o Gemini")):
                raise
            if informar:
                informar("Sugestões temporariamente indisponíveis; buscando pelo estilo no Spotify…")
            break
        titulos_anteriores.extend(s["titulo"] for s in sugestoes)
        validas, _ = validar_musicas(sugestoes, token)
        for musica in validas:
            incluir(musica)

    if len(selecionadas) < tamanho:
        if informar:
            informar("Completando com músicas do estilo e de artistas próximos…")
        plano_catalogo = dict(plano)
        plano_catalogo["artistas"] = list(dict.fromkeys(
            [referencia["artista"], *plano["artistas"]]
        ))
        plano_catalogo["artistas_pedidos"] = []
        candidatas, _ = pesquisar_musicas(
            plano_catalogo, token,
            quantidade=min(100, max(tamanho * 2, tamanho + len(selecionadas))),
            paginas=3, limite_por_artista=5,
        )
        for musica in candidatas:
            incluir(musica)
    if len(selecionadas) < tamanho and plano["generos"]:
        # Só amplia para o gênero específico se os artistas próximos não bastarem.
        plano_genero = {**plano, "artistas": [], "artistas_pedidos": [],
                        "generos": plano["generos"][:1]}
        candidatas, _ = pesquisar_musicas(
            plano_genero, token, quantidade=min(100, tamanho * 2),
            paginas=5, limite_por_artista=5, permitir_genero_livre=True,
        )
        for musica in candidatas:
            incluir(musica)
    return selecionadas


def _publicar_playlist(mood, musicas):
    nome = " ".join(mood.split())
    nome = nome if len(nome) <= 60 else nome[:57].rstrip() + "..."
    playlist = criar_playlist(obter_token_valido(), nome, mood)
    link = playlist.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/playlist/{playlist['id']}"
    try:
        adicionar_musicas(obter_token_valido(), playlist["id"], [m["uri"] for m in musicas])
    except (RuntimeError, requests.RequestException, OSError, ValueError):
        print(f"A playlist foi criada, mas a inclusão não foi concluída. Ela pode estar vazia ou parcial: {link}", file=sys.stderr)
        raise
    return {"nome": nome, "link": link, "musicas": musicas, "quantidade": len(musicas)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Cria uma playlist privada por mood")
    parser.add_argument("mood", help="Descrição do mood (até 300 caracteres)")
    parser.add_argument("--tamanho", type=int, default=20, help="Quantidade desejada (padrão: 20)")
    args = parser.parse_args(argv)
    if not args.mood.strip() or len(args.mood) > 300:
        parser.error("O mood deve ter de 1 a 300 caracteres para caber inteiro na descrição do Spotify.")
    if args.tamanho <= 0:
        parser.error("--tamanho deve ser um inteiro positivo.")
    try:
        resultado = criar_playlist_por_mood(args.mood, args.tamanho)
        if resultado["quantidade"] < args.tamanho:
            print(f"Foram validadas {resultado['quantidade']} de {args.tamanho} músicas; a playlist terá essa quantidade.")
        for musica in resultado["musicas"]:
            print(f"{musica['artista']} – {musica['titulo']}")
        print(f"Playlist privada: {resultado['link']}")
        return 0
    except (RuntimeError, requests.RequestException, OSError, ValueError) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
