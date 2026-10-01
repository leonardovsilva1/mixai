import json
from unittest.mock import Mock

import pytest

import main
from app import busca, ia


def plano():
    return {"tipo": "mood_artista", "referencia": None, "artistas_pedidos": ["Derek"], "artistas": ["Derek"], "generos": [], "termos_mood": ["trap hard"], "ano_inicio": 2022, "ano_fim": 2022}


def faixa(numero, artista="Derek", ano="2022"):
    return {"name": f"Faixa {numero}", "uri": f"spotify:track:{numero}",
            "artists": [{"name": artista}], "album": {"release_date": ano},
            "external_urls": {"spotify": f"https://open.spotify.com/track/{numero}"}}


@pytest.fixture
def rede(monkeypatch):
    chamada = Mock()
    monkeypatch.setattr(busca, "requisitar", chamada)
    monkeypatch.setattr(busca.time, "sleep", Mock())
    return chamada


def resposta(itens, proxima=None):
    r = Mock()
    r.json.return_value = {"tracks": {"items": itens, "next": proxima}}
    return r


def test_resolve_grafia_do_artista_pelo_primeiro_perfil_relevante(rede):
    rede.return_value.json.return_value = {"artists": {"items": [
        {"name": "Phl Notunrboy"}, {"name": "PHL Noturnboy"},
    ]}}
    assert busca.resolver_artista("Phl Noturnboy", "token") == "Phl Notunrboy"
    assert rede.call_args.kwargs["params"] == {
        "q": "Phl Noturnboy", "type": "artist", "limit": 10,
    }


def test_paginacao_filtro_ano_artista(rede):
    rede.side_effect = [resposta([faixa(1, ano="2023"), faixa(2, artista="Outro")], "pagina"),
                        resposta([faixa(3)])]
    musicas, total = busca.pesquisar_musicas(plano(), "token")
    assert [m["titulo"] for m in musicas] == ["Faixa 3"]
    assert total == 3
    assert rede.call_args_list[0].kwargs["params"] == {
        "q": 'artist:"Derek" trap hard year:2022-2022', "type": "track", "limit": 10, "offset": 0}
    assert rede.call_args_list[1].kwargs["params"]["offset"] == 10


def test_fallback_para_artista_quando_termos_do_mood_nao_sao_indexados(rede):
    rede.side_effect = [resposta([]), resposta([faixa(7)])]
    musicas, _ = busca.pesquisar_musicas(plano(), "token")
    assert [m["titulo"] for m in musicas] == ["Faixa 7"]
    assert rede.call_args_list[0].kwargs["params"]["q"] == 'artist:"Derek" trap hard year:2022-2022'
    assert rede.call_args_list[1].kwargs["params"]["q"] == 'artist:"Derek" year:2022-2022'


def test_diversidade_duplicatas_limites(rede):
    p = plano()
    p["artistas"].append("Outro")
    rede.side_effect = [resposta([faixa(i) for i in range(5)]),
                        resposta([faixa(10, "Outro"), faixa(10, "Outro"), faixa(11, "Outro")])]
    musicas, _ = busca.pesquisar_musicas(p, "token")
    assert [m["titulo"] for m in musicas] == ["Faixa 0", "Faixa 10", "Faixa 1", "Faixa 2", "Faixa 11"]


def test_deduplica_isrc_e_titulo(rede):
    a, b, c, d = [faixa(i) for i in range(4)]
    a["external_ids"] = b["external_ids"] = {"isrc": "MESMA-GRAVACAO"}
    c["name"] = "Faixa 0 (Remastered)"
    d["is_playable"] = False
    rede.return_value = resposta([a, b, c, d])
    musicas, _ = busca.pesquisar_musicas(plano(), "token")
    assert len(musicas) == 1


def test_limite_paginas_e_quantidade(rede):
    rede.return_value = resposta([faixa(1)], "continua")
    musicas, _ = busca.pesquisar_musicas(plano(), "token", quantidade=1, paginas=2)
    assert len(musicas) == 1
    assert rede.call_count == 2


def test_quota_interrompe(rede):
    rede.side_effect = RuntimeError("Cota excedida")
    with pytest.raises(RuntimeError, match="Cota"):
        busca.pesquisar_musicas(plano(), "token")
    assert rede.call_count == 1


def test_genero_amplo_respeita_referencias(rede):
    p = plano()
    p["generos"] = ["gangsta rap"]
    p["termos_mood"] = []
    rede.side_effect = [resposta([]), resposta([faixa(1, "Artista fora do contexto"), faixa(2)])]
    musicas, _ = busca.pesquisar_musicas(p, "token")
    assert [m["titulo"] for m in musicas] == ["Faixa 2"]


def test_referencia_pode_explorar_artistas_encontrados_pelo_genero(rede):
    p = plano()
    p["generos"] = ["witch house"]
    p["termos_mood"] = []
    rede.side_effect = [resposta([]), resposta([faixa(1, "Artista novo")])]
    musicas, _ = busca.pesquisar_musicas(p, "token", permitir_genero_livre=True)
    assert [m["artista"] for m in musicas] == ["Artista novo"]


def test_plano_valido():
    assert ia.validar_plano(json.dumps(plano())) == plano()


def test_artista_com_atributos_sonoros_e_pedido_combinado():
    pedido = {**plano(), "tipo": "artista", "termos_mood": ["hard", "grave alto"]}
    assert ia.validar_plano(json.dumps(pedido))["tipo"] == "mood_artista"


def test_plano_faixa_de_referencia_separa_artista_exigido():
    pedido = {
        **plano(), "tipo": "referencia", "artistas_pedidos": [],
        "referencia": {"titulo": " Goth ", "artista": " Sidewalks and Skeletons "},
        "artistas": ["Pastel Ghost", "Crystal Castles"],
        "generos": ["witch house", "darkwave"],
    }
    resultado = ia.validar_plano(json.dumps(pedido))
    assert resultado["referencia"] == {"titulo": "Goth", "artista": "Sidewalks and Skeletons"}
    assert resultado["artistas_pedidos"] == []
    assert resultado["artistas"] == ["Pastel Ghost", "Crystal Castles"]


@pytest.mark.parametrize("mudanca", [
    {"referencia": None},
    {"referencia": {"titulo": "", "artista": "Sidewalks and Skeletons"}},
    {"artistas_pedidos": ["Sidewalks and Skeletons"]},
    {"artistas": ["Sidewalks and Skeletons"]},
])
def test_plano_referencia_invalido(mudanca):
    pedido = {
        **plano(), "tipo": "referencia", "artistas_pedidos": [],
        "referencia": {"titulo": "Goth", "artista": "Sidewalks and Skeletons"},
        "artistas": ["Pastel Ghost"],
        **mudanca,
    }
    with pytest.raises(ValueError):
        ia.validar_plano(json.dumps(pedido))


@pytest.mark.parametrize("mudanca", [
    {"ano_inicio": 2023}, {"ano_fim": None}, {"ano_inicio": True},
    {"artistas": [""]}, {"generos": "trap"}, {"titulo": "inventado"},
    {"tipo": "artista", "artistas_pedidos": []}, {"tipo": "mood", "artistas_pedidos": ["Derek"]},
])
def test_plano_invalido(mudanca):
    with pytest.raises(ValueError):
        ia.validar_plano(json.dumps({**plano(), **mudanca}))
