from unittest.mock import Mock

import pytest
import requests

import main
from app import spotify_api as api


def musica(n, uri=None):
    return {"artista": f"Artista {n}", "titulo": f"Titulo {n}", "ano": 2022,
            "nota": "Nota curta.", "uri": uri or f"spotify:track:{n}"}


@pytest.fixture
def fluxo(monkeypatch):
    mocks = {}
    for nome in ("verificar_cota_spotify", "obter_token_valido", "planejar_busca", "resolver_artista", "pesquisar_musicas", "sugerir_musicas", "validar_musicas", "criar_playlist", "adicionar_musicas"):
        mocks[nome] = Mock()
        monkeypatch.setattr(main, nome, mocks[nome])
    mocks["obter_token_valido"].return_value = "token"
    mocks["resolver_artista"].side_effect = lambda nome, token: nome
    mocks["planejar_busca"].return_value = {"tipo": "mood", "artistas_pedidos": [], "artistas": [], "generos": [], "termos_mood": [], "ano_inicio": None, "ano_fim": None}
    mocks["sugerir_musicas"].return_value = []
    mocks["validar_musicas"].return_value = ([], [])
    mocks["criar_playlist"].return_value = {"id": "lista", "external_urls": {"spotify": "https://open.spotify.com/playlist/lista"}}
    return mocks


def test_primeiras_n_uma_rodada(fluxo, capsys):
    itens = [musica(i) for i in range(3)]
    fluxo["sugerir_musicas"].return_value = itens
    fluxo["validar_musicas"].return_value = (itens, [])
    assert main.main(["calmo", "--tamanho", "2"]) == 0
    fluxo["sugerir_musicas"].assert_called_once_with("calmo", 3, titulos_anteriores=[])
    fluxo["criar_playlist"].assert_called_once_with("token", "calmo", "calmo")
    fluxo["adicionar_musicas"].assert_called_once_with("token", "lista", ["spotify:track:0", "spotify:track:1"])
    assert "https://open.spotify.com/playlist/lista" in capsys.readouterr().out


def test_previa_aproveita_sobras_sem_novas_buscas(fluxo):
    itens = [musica(i) for i in range(25)]
    fluxo["sugerir_musicas"].return_value = itens
    fluxo["validar_musicas"].return_value = (itens, [])
    resultado = main.selecionar_musicas_por_mood("calmo", 20, reservas=5)
    assert len(resultado) == 25
    fluxo["sugerir_musicas"].assert_called_once()
    fluxo["validar_musicas"].assert_called_once()
    fluxo["criar_playlist"].assert_not_called()


def test_cota_conhecida_para_antes_do_gemini(fluxo):
    from app.spotify_api import CotaSpotifyExcedida
    fluxo["verificar_cota_spotify"].side_effect = CotaSpotifyExcedida("Cota excedida")
    with pytest.raises(CotaSpotifyExcedida):
        main.selecionar_musicas_por_mood("calmo", 20)
    fluxo["obter_token_valido"].assert_not_called()
    fluxo["planejar_busca"].assert_not_called()


def test_segunda_rodada_so_titulos_da_ia(fluxo):
    primeiro = {k: v for k, v in musica(1).items() if k != "uri"}
    segundo = {k: v for k, v in musica(2).items() if k != "uri"}
    fluxo["sugerir_musicas"].side_effect = [[primeiro], [primeiro, segundo]]
    fluxo["validar_musicas"].side_effect = [([musica(1, "spotify:track:catalogo")], []), ([musica(2)], [])]
    assert main.main(["mood", "--tamanho", "2"]) == 0
    assert fluxo["sugerir_musicas"].call_args.kwargs == {"titulos_anteriores": ["Titulo 1"]}
    assert fluxo["sugerir_musicas"].call_args.args == ("mood", 2)
    assert fluxo["validar_musicas"].call_args.args[0] == [segundo]


def test_parcial_e_uri_duplicada(fluxo, capsys):
    fluxo["sugerir_musicas"].side_effect = [[musica(1)], [musica(2)]]
    fluxo["validar_musicas"].side_effect = [([musica(1)], []), ([musica(2, "spotify:track:1")], [])]
    assert main.main(["mood", "--tamanho", "3"]) == 0
    assert fluxo["sugerir_musicas"].call_count == 2
    fluxo["adicionar_musicas"].assert_called_once_with("token", "lista", ["spotify:track:1"])
    assert "1 de 3" in capsys.readouterr().out


def test_zero_nao_cria(fluxo):
    fluxo["sugerir_musicas"].return_value = [musica(1)]
    fluxo["validar_musicas"].return_value = ([], [])
    assert main.main(["mood"]) == 1
    assert fluxo["sugerir_musicas"].call_count == 2
    assert fluxo["sugerir_musicas"].call_args_list[0].args[1] == 30
    fluxo["criar_playlist"].assert_not_called()


def test_falha_apos_criar_mostra_link(fluxo, capsys):
    fluxo["sugerir_musicas"].return_value = [musica(1)]
    fluxo["validar_musicas"].return_value = ([musica(1)], [])
    fluxo["adicionar_musicas"].side_effect = requests.Timeout("timeout")
    assert main.main(["mood", "--tamanho", "1"]) == 1
    assert "https://open.spotify.com/playlist/lista" in capsys.readouterr().err
    assert fluxo["criar_playlist"].call_count == 1


def test_nome_curto_descricao_inteira(fluxo):
    mood = "calmo " * 20
    fluxo["sugerir_musicas"].return_value = [musica(1)]
    fluxo["validar_musicas"].return_value = ([musica(1)], [])
    assert main.main([mood, "--tamanho", "1"]) == 0
    args = fluxo["criar_playlist"].call_args.args
    assert len(args[1]) <= 60
    assert args[2] == mood


@pytest.mark.parametrize("args", [[""], ["x" * 301], ["mood", "--tamanho", "0"], ["mood", "--tamanho", "-1"]])
def test_argumentos_invalidos(fluxo, args):
    with pytest.raises(SystemExit):
        main.main(args)
    fluxo["obter_token_valido"].assert_not_called()


def test_endpoints_e_lotes(monkeypatch):
    chamada = Mock()
    chamada.return_value.json.return_value = {"id": "lista"}
    monkeypatch.setattr(api, "requisitar", chamada)
    assert api.criar_playlist("token", "Nome", "Mood completo") == {"id": "lista"}
    assert chamada.call_args.args == ("POST", "https://api.spotify.com/v1/me/playlists")
    assert chamada.call_args.kwargs["json"] == {"name": "Nome", "description": "Mood completo", "public": False}
    chamada.reset_mock()
    uris = [f"spotify:track:{i}" for i in range(205)]
    api.adicionar_musicas("token", "lista", uris)
    assert [len(c.kwargs["json"]["uris"]) for c in chamada.call_args_list] == [100, 100, 5]
    assert all(c.args == ("POST", "https://api.spotify.com/v1/playlists/lista/items") for c in chamada.call_args_list)


def test_pedido_com_artista_usa_artista_e_mood_na_busca(fluxo):
    fluxo["planejar_busca"].return_value = {
        "tipo": "mood_artista", "artistas_pedidos": ["PHL"],
        "artistas": ["Artista pr?ximo"], "generos": ["trap"],
        "termos_mood": ["hard", "808 alto"], "ano_inicio": 2026, "ano_fim": 2026,
    }
    itens = [musica(1)]
    itens[0]["artista"] = "PHL"
    fluxo["pesquisar_musicas"].return_value = (itens, 1)
    resultado = main.criar_playlist_por_mood("trap hard com PHL", 1)
    fluxo["pesquisar_musicas"].assert_called_once()
    plano_enviado = fluxo["pesquisar_musicas"].call_args_list[0].args[0]
    assert plano_enviado["artistas_pedidos"] == ["PHL"]
    assert plano_enviado["artistas"] == ["PHL"]
    assert plano_enviado["termos_mood"] == ["hard", "808 alto"]
    assert fluxo["pesquisar_musicas"].call_args.kwargs["limite_por_artista"] is None
    assert resultado["musicas"] == itens
    assert fluxo["sugerir_musicas"].call_count == 2
    assert fluxo["sugerir_musicas"].call_args.kwargs["artistas_pedidos"] == ["PHL"]


def test_pedido_com_mood_e_artista_nao_limita_artista_explicito(fluxo):
    fluxo["planejar_busca"].return_value = {
        "tipo": "mood_artista", "artistas_pedidos": ["PHL"], "artistas": [],
        "generos": [], "termos_mood": ["hard", "808"],
        "ano_inicio": None, "ano_fim": None,
    }
    faixas = [musica(i, uri=f"spotify:track:{i}") for i in range(20)]
    for faixa in faixas:
        faixa["artista"] = "PHL"
    fluxo["pesquisar_musicas"].return_value = (faixas, 20)
    resultado = main.criar_playlist_por_mood("PHL hard 808", 20)
    assert len(resultado["musicas"]) == 20
    assert fluxo["sugerir_musicas"].call_count == 2
    assert fluxo["pesquisar_musicas"].call_args.kwargs["quantidade"] == 20
    assert fluxo["pesquisar_musicas"].call_args.kwargs["limite_por_artista"] is None


def test_grafia_resolvida_no_spotify_sem_enviar_dados_do_spotify_ao_gemini(fluxo):
    fluxo["planejar_busca"].return_value = {
        "tipo": "mood_artista", "artistas_pedidos": ["Phl Noturnboy"],
        "artistas": [], "generos": ["trap"], "termos_mood": ["hard", "grave alto"],
        "ano_inicio": None, "ano_fim": None,
    }
    fluxo["resolver_artista"].side_effect = None
    fluxo["resolver_artista"].return_value = "Phl Notunrboy"
    faixas = [{**musica(i), "artista": "Phl Notunrboy"} for i in range(20)]
    fluxo["pesquisar_musicas"].return_value = (faixas, 30)
    resultado = main.criar_playlist_por_mood("quero ouvir phl noturnboy as mais hard com grave alto", 20)
    assert resultado["quantidade"] == 20
    assert fluxo["sugerir_musicas"].call_args.kwargs["artistas_pedidos"] == ["Phl Noturnboy"]
    plano_spotify = fluxo["pesquisar_musicas"].call_args.args[0]
    assert plano_spotify["artistas"] == ["Phl Notunrboy"]


def test_pedido_so_artista_busca_discografia(fluxo):
    fluxo["planejar_busca"].return_value = {
        "tipo": "artista", "artistas_pedidos": ["Guap508"], "artistas": [],
        "generos": [], "termos_mood": [], "ano_inicio": None, "ano_fim": None,
    }
    fluxo["pesquisar_musicas"].return_value = ([musica(1)], 1)
    main.criar_playlist_por_mood("Guap508", 1)
    argumentos = fluxo["pesquisar_musicas"].call_args
    assert argumentos.args[0]["artistas"] == ["Guap508"]
    assert argumentos.kwargs["limite_por_artista"] is None


def test_referencia_completa_vinte_faixas_de_varios_artistas(fluxo):
    fluxo["planejar_busca"].return_value = {
        "tipo": "referencia",
        "referencia": {"titulo": "Goth", "artista": "Sidewalks and Skeletons"},
        "artistas_pedidos": [], "artistas": ["Salem", "Pastel Ghost"],
        "generos": ["witch house"], "termos_mood": ["sombrio"],
        "ano_inicio": None, "ano_fim": None,
    }
    original = {**musica(0), "artista": "Sidewalks and Skeletons", "titulo": "Goth"}
    fluxo["validar_musicas"].side_effect = [([original], []), ([], []), ([], [])]
    catalogo = [{**musica(i + 1), "artista": f"Artista {i // 5}"} for i in range(19)]
    fluxo["pesquisar_musicas"].return_value = (catalogo, 40)
    resultado = main.criar_playlist_por_mood("parecidas com Goth", 20)
    assert resultado["quantidade"] == 20
    assert resultado["musicas"][0]["titulo"] == "Goth"
    assert len({m["artista"] for m in resultado["musicas"]}) == 5
    assert fluxo["pesquisar_musicas"].call_args.kwargs.get("permitir_genero_livre", False) is False
    assert fluxo["sugerir_musicas"].call_args.kwargs["referencia"]["titulo"] == "Goth"


def test_referencia_nao_cria_playlist_parcial(fluxo):
    fluxo["planejar_busca"].return_value = {
        "tipo": "referencia",
        "referencia": {"titulo": "Goth", "artista": "Sidewalks and Skeletons"},
        "artistas_pedidos": [], "artistas": ["Salem"],
        "generos": ["witch house"], "termos_mood": ["sombrio"],
        "ano_inicio": None, "ano_fim": None,
    }
    fluxo["pesquisar_musicas"].return_value = ([musica(i) for i in range(12)], 12)
    with pytest.raises(RuntimeError, match="apenas 12"):
        main.criar_playlist_por_mood("parecidas com Goth", 20)
    assert fluxo["pesquisar_musicas"].call_args.kwargs["permitir_genero_livre"] is True
    fluxo["criar_playlist"].assert_not_called()


def test_referencia_continua_no_spotify_se_gemini_tem_erro_temporario(fluxo):
    fluxo["planejar_busca"].return_value = {
        "tipo": "referencia",
        "referencia": {"titulo": "Goth", "artista": "Sidewalks and Skeletons"},
        "artistas_pedidos": [], "artistas": ["Salem", "Pastel Ghost"],
        "generos": ["witch house"], "termos_mood": ["sombrio"],
        "ano_inicio": None, "ano_fim": None,
    }
    fluxo["sugerir_musicas"].side_effect = RuntimeError("O Gemini está indisponível no momento.")
    fluxo["pesquisar_musicas"].return_value = (
        [{**musica(i), "artista": f"Artista {i // 5}"} for i in range(20)], 40,
    )
    resultado = main.criar_playlist_por_mood("parecidas com Goth", 20)
    assert resultado["quantidade"] == 20
    assert fluxo["sugerir_musicas"].call_count == 1
