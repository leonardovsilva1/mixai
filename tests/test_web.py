from unittest.mock import Mock

import pytest

from app.web import PREVIAS, app


@pytest.fixture
def cliente():
    app.config.update(TESTING=True)
    PREVIAS.clear()
    with app.test_client() as cliente:
        yield cliente
    PREVIAS.clear()


def test_home_renderiza_previa_e_formulario(cliente):
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert b"O que voc\xc3\xaa" in resposta.data
    assert b"Ver pr\xc3\xa9via da playlist" in resposta.data
    assert b"logo.svg" in resposta.data


@pytest.mark.parametrize("dados", [
    {}, {"mood": "  ", "tamanho": 20}, {"mood": "x" * 301, "tamanho": 20},
    {"mood": "calmo", "tamanho": True}, {"mood": "calmo", "tamanho": 0},
    {"mood": "calmo", "tamanho": 101},
])
def test_validacao_form(cliente, dados):
    resposta = cliente.post("/api/previews", json=dados)
    assert resposta.status_code == 400
    assert "erro" in resposta.json


def test_previa_nao_cria_playlist_e_confirma_escolha(cliente, monkeypatch):
    import app.web as web
    musicas = [
        {"artista": "Artista", "titulo": f"Faixa {i}", "uri": f"spotify:track:{i}"}
        for i in range(3)
    ]
    selecionar = Mock(return_value=musicas)
    publicar = Mock(return_value={"nome": "Calmo", "link": "https://open.spotify.com/playlist/1",
                                  "quantidade": 1, "musicas": [musicas[2]]})
    monkeypatch.setattr(web, "selecionar_musicas_por_mood", selecionar)
    monkeypatch.setattr(web, "_publicar_playlist", publicar)
    previa = cliente.post("/api/previews", json={"mood": " calmo ", "tamanho": 1})
    assert previa.status_code == 200
    assert len(previa.json["musicas"]) == 1
    assert len(previa.json["alternativas"]) == 2
    assert "uri" not in previa.json["musicas"][0]
    publicar.assert_not_called()
    selecionar.assert_called_once_with("calmo", 1, exigir_quantidade=False, reservas=5)
    alternativa = previa.json["alternativas"][1]["id"]
    resposta = cliente.post("/api/playlists", json={"previa": previa.json["previa"], "faixas": [alternativa]})
    assert resposta.status_code == 200
    assert resposta.json["musicas"] == [{"artista": "Artista", "titulo": "Faixa 2"}]
    assert resposta.json["link"].endswith("/1")
    publicar.assert_called_once_with("calmo", [musicas[2]])
    assert cliente.post("/api/playlists", json={"previa": previa.json["previa"], "faixas": [alternativa]}).status_code == 410


def test_erro_backend(cliente, monkeypatch):
    import app.web as web
    monkeypatch.setattr(web, "selecionar_musicas_por_mood", Mock(side_effect=RuntimeError("Nenhuma música validada")))
    resposta = cliente.post("/api/previews", json={"mood": "mood", "tamanho": 10})
    assert resposta.status_code == 502
    assert "Nenhuma música validada" in resposta.json["erro"]


def test_cota_spotify_responde_429(cliente, monkeypatch):
    import app.web as web
    from app.spotify_api import CotaSpotifyExcedida
    monkeypatch.setattr(web, "selecionar_musicas_por_mood", Mock(
        side_effect=CotaSpotifyExcedida("Cota de uso da API do Spotify excedida.")
    ))
    resposta = cliente.post("/api/previews", json={"mood": "calmo", "tamanho": 20})
    assert resposta.status_code == 429
    assert "Cota" in resposta.json["erro"]


def test_confirmacao_rejeita_faixas_forgadas_repetidas_ou_vazias(cliente, monkeypatch):
    import app.web as web
    publicar = Mock()
    monkeypatch.setattr(web, "_publicar_playlist", publicar)
    monkeypatch.setattr(web, "selecionar_musicas_por_mood", Mock(return_value=[
        {"artista": "A", "titulo": "B", "uri": "spotify:track:1"},
    ]))
    previa = cliente.post("/api/previews", json={"mood": "mood", "tamanho": 2}).json
    valido = previa["musicas"][0]["id"]
    for ids in ([], ["forjado"], [valido, valido]):
        resposta = cliente.post("/api/playlists", json={"previa": previa["previa"], "faixas": ids})
        assert resposta.status_code == 400
    publicar.assert_not_called()


def test_sem_previa_nao_cria_playlist(cliente):
    resposta = cliente.post("/api/playlists", json={"mood": "mood", "tamanho": 2})
    assert resposta.status_code == 400
