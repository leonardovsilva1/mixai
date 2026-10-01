from unittest.mock import Mock

import pytest
import requests

from app import validacao as v


@pytest.mark.parametrize("texto, esperado", [
    ("  CANÇÃO, DE AMOR! ", "cancao de amor"),
    ("A Carta - Remastered 2010", "a carta"),
    ("A Carta – 2010 Remaster", "a carta"),
    ("A Carta Ao Vivo", "a carta"),
    ("A Carta - Ao Vivo em São Paulo", "a carta"),
    ("A Carta (Ao Vivo (1999))", "a carta"),
    ("A Carta [Remastered]", "a carta"),
    ("Amor - Saudade", "amor saudade"),
    ("(Ao Vivo)", ""),
])
def test_normalizacao(texto, esperado):
    assert v.normalizar_texto(texto) == esperado


def faixa(titulo="A Carta", artista="Waldick Soriano", uri="spotify:track:1"):
    return {"name": titulo, "artists": [{"name": artista}], "uri": uri}


def sugestao(titulo="A Carta", artista="Waldick Soriano"):
    return {"titulo": titulo, "artista": artista, "ano": 1965, "nota": "Uma canção triste."}


def test_comparacao():
    assert v.similaridade("Canção", "CANCAO (Remastered)") == 1
    assert v.similaridade("", "") == 0
    assert v.similaridade("A Carta", "Gato Triste") < 0.85


def test_ambos_campos_precisam_bater():
    assert v.melhor_correspondencia(sugestao(), [faixa(artista="Roberto Carlos")]) is None
    assert v.melhor_correspondencia(sugestao(), [faixa(titulo="Gato Triste")]) is None


def test_melhor_resultado_e_limiar():
    aproximada, exata = faixa(titulo="A Cartas"), faixa(uri="spotify:track:2")
    assert v.melhor_correspondencia(sugestao(), [aproximada, exata]) == exata
    assert v.melhor_correspondencia(sugestao(), [aproximada], 0.8) == aproximada
    assert v.melhor_correspondencia(sugestao(), [aproximada], 1) is None


def test_multiplos_artistas():
    resultado = faixa(artista="Outro")
    resultado["artists"].append({"name": "Waldick Soriano"})
    assert v.melhor_correspondencia(sugestao(), [resultado]) == resultado


@pytest.fixture
def rede(monkeypatch):
    chamada, pausa = Mock(), Mock()
    monkeypatch.setattr(v, "requisitar", chamada)
    monkeypatch.setattr(v.time, "sleep", pausa)
    return chamada, pausa


def resposta(itens):
    r = Mock()
    r.json.return_value = {"tracks": {"items": itens}}
    return r


def test_busca_alternativa_e_pausa(rede):
    chamada, pausa = rede
    chamada.side_effect = [resposta([]), resposta([faixa()])]
    validas, descartadas = v.validar_musicas([sugestao()], "token")
    assert validas == [{**sugestao(), "uri": "spotify:track:1"}]
    assert descartadas == []
    assert chamada.call_args_list[0].kwargs["params"] == {
        "q": "track:A Carta artist:Waldick Soriano", "type": "track", "limit": 5}
    assert chamada.call_args_list[1].kwargs["params"]["q"] == "A Carta Waldick Soriano"
    pausa.assert_called_once_with(0.2)


@pytest.mark.parametrize("itens, motivo, chamadas", [
    ([], "Nenhum resultado encontrado", 2),
    ([faixa(artista="Outro")], "suficientemente parecidos", 1),
])
def test_descarte(rede, itens, motivo, chamadas):
    chamada, _ = rede
    chamada.return_value = resposta(itens)
    validas, descartadas = v.validar_musicas([sugestao()], "token")
    assert not validas
    assert motivo in descartadas[0]["motivo"]
    assert chamada.call_count == chamadas


def test_duplicata_normalizada(rede):
    chamada, _ = rede
    chamada.return_value = resposta([faixa()])
    validas, descartadas = v.validar_musicas([sugestao(), sugestao("A Carta (Remastered)")], "token")
    assert len(validas) == len(descartadas) == 1
    assert "duplicada" in descartadas[0]["motivo"]
    assert chamada.call_count == 1


@pytest.mark.parametrize("uri", ["spotify:track:1", "spotify:track:2"])
def test_duplicata_resultado(rede, uri):
    chamada, _ = rede
    chamada.side_effect = [resposta([faixa()]), resposta([faixa(uri=uri)])]
    validas, descartadas = v.validar_musicas([sugestao(), sugestao("A Cartas")], "token")
    assert len(validas) == len(descartadas) == 1
    assert "duplicada no Spotify" in descartadas[0]["motivo"]


def test_erro_api_interrompe(rede):
    chamada, _ = rede
    chamada.side_effect = RuntimeError("Cota do Spotify excedida.")
    with pytest.raises(RuntimeError, match="Cota"):
        v.validar_musicas([sugestao(), sugestao("Outra")], "token")
    assert chamada.call_count == 1


def test_erro_http_nao_vira_descarte(rede):
    chamada, _ = rede
    chamada.return_value.raise_for_status.side_effect = requests.HTTPError("401")
    with pytest.raises(requests.HTTPError):
        v.validar_musicas([sugestao()], "token")


@pytest.mark.parametrize("limiar", [-1, 0, 1.1, float("nan")])
def test_limiar_invalido(rede, limiar):
    with pytest.raises(ValueError):
        v.validar_musicas([], "token", limiar=limiar)
    rede[0].assert_not_called()
