import base64
import hashlib
import json
import threading
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock

import pytest
import requests

import main
from app import spotify_api as api
from app import spotify_auth as auth


@pytest.fixture(autouse=True)
def cache_cota_isolado(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "COTA_CACHE", tmp_path / "cota.json")
    monkeypatch.setattr(main, "verificar_cota_spotify", Mock())


def resposta(status=200, dados=None, headers=None):
    valor = requests.Response()
    valor.status_code = status
    valor._content = json.dumps(dados or {}).encode()
    valor.headers.update(headers or {})
    return valor


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "CACHE", tmp_path / "token.json")
    monkeypatch.setattr(auth, "load_dotenv", lambda *args: None)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cliente-teste")
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    return auth.CACHE


def salvar_cache(caminho, expires_at=900):
    caminho.write_text(json.dumps({
        "client_id": "cliente-teste", "scope": auth.SCOPES,
        "access_token": "antigo", "refresh_token": "refresh-antigo",
        "expires_at": expires_at,
    }))


def test_cache_valido(ambiente, monkeypatch):
    salvar_cache(ambiente, 2000)
    chamada = Mock(side_effect=AssertionError("Não deve chamar a rede"))
    monkeypatch.setattr(auth, "requisitar", chamada)
    assert auth.obter_token_valido() == "antigo"


@pytest.mark.parametrize("rotacionar", [False, True])
def test_renova_token(ambiente, monkeypatch, rotacionar):
    salvar_cache(ambiente)
    dados = {"access_token": "novo", "expires_in": 3600}
    if rotacionar:
        dados["refresh_token"] = "refresh-novo"
    chamada = Mock(return_value=resposta(dados=dados))
    monkeypatch.setattr(auth, "requisitar", chamada)
    assert auth.obter_token_valido() == "novo"
    cache = json.loads(ambiente.read_text())
    assert cache["expires_at"] == 4600
    assert cache["refresh_token"] == ("refresh-novo" if rotacionar else "refresh-antigo")
    assert chamada.call_args.kwargs["data"]["grant_type"] == "refresh_token"


def test_refresh_recusado_refaz_login(ambiente, monkeypatch):
    salvar_cache(ambiente)
    monkeypatch.setattr(auth, "requisitar", Mock(return_value=resposta(400, {"error": "invalid_grant"})))
    login = Mock(return_value={"access_token": "novo", "refresh_token": "novo-refresh", "expires_in": 3600})
    monkeypatch.setattr(auth, "_login", login)
    assert auth.obter_token_valido() == "novo"
    login.assert_called_once_with("cliente-teste")


def test_falha_transitoria_preserva_cache(ambiente, monkeypatch):
    salvar_cache(ambiente)
    antes = ambiente.read_bytes()
    monkeypatch.setattr(auth, "requisitar", Mock(return_value=resposta(503)))
    with pytest.raises(requests.HTTPError):
        auth.obter_token_valido()
    assert ambiente.read_bytes() == antes


@pytest.mark.parametrize("conteudo", ["{", "[]", '{}'])
def test_cache_invalido_abre_login(ambiente, monkeypatch, conteudo):
    ambiente.write_text(conteudo)
    monkeypatch.setattr(auth, "_login", Mock(return_value={
        "access_token": "novo", "refresh_token": "refresh", "expires_in": 3600,
    }))
    assert auth.obter_token_valido() == "novo"


def test_pkce(monkeypatch):
    capturado = {}

    def receber(url, state):
        capturado.update(parse_qs(urlsplit(url).query))
        assert capturado["state"] == [state]
        return "codigo-teste"

    monkeypatch.setattr(auth, "_receber_codigo", receber)
    chamada = Mock(return_value=resposta(dados={"access_token": "novo"}))
    monkeypatch.setattr(auth, "requisitar", chamada)
    auth._login("cliente-teste")
    dados = chamada.call_args.kwargs["data"]
    esperado = base64.urlsafe_b64encode(hashlib.sha256(dados["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert capturado["code_challenge"] == [esperado]
    assert capturado["code_challenge_method"] == ["S256"]
    assert capturado["scope"] == [auth.SCOPES]
    assert dados["redirect_uri"] == auth.REDIRECT_URI
    assert "client_secret" not in dados


@pytest.mark.parametrize("negado", [False, True])
def test_callback_local(monkeypatch, negado):
    resultados = []
    falhas = []

    def navegador(url):
        def visitar():
            try:
                sessao = requests.Session()
                sessao.trust_env = False
                resultados.append(sessao.get(auth.REDIRECT_URI + "?state=errado&code=ignorado", timeout=5).status_code)
                parametros = {"state": "estado-teste"}
                parametros.update({"error": "access_denied"} if negado else {"code": "codigo-teste"})
                resultados.append(sessao.get(auth.REDIRECT_URI + "?" + urlencode(parametros), timeout=5).status_code)
            except Exception as erro:
                falhas.append(erro)
        thread = threading.Thread(target=visitar)
        threads.append(thread)
        thread.start()
        return True

    threads = []
    monkeypatch.setattr(auth.webbrowser, "open", navegador)
    if negado:
        with pytest.raises(RuntimeError, match="recusado"):
            auth._receber_codigo("https://example.com", "estado-teste")
    else:
        assert auth._receber_codigo("https://example.com", "estado-teste") == "codigo-teste"
    for thread in threads:
        thread.join(6)
    assert not falhas
    assert resultados == [400, 400 if negado else 200]


def test_429_respeita_retry_after(monkeypatch):
    chamada = Mock(side_effect=[resposta(429, headers={"Retry-After": "7"}), resposta(dados={"id": "usuario"})])
    pausa = Mock()
    monkeypatch.setattr(api.requests, "request", chamada)
    monkeypatch.setattr(api.time, "sleep", pausa)
    assert api.obter_perfil("token")["id"] == "usuario"
    pausa.assert_called_once_with(7)
    assert chamada.call_args.args == ("GET", "https://api.spotify.com/v1/me")
    assert chamada.call_args.kwargs["headers"] == {"Authorization": "Bearer token"}


def test_502_repetido_em_busca_get(monkeypatch):
    chamada = Mock(side_effect=[resposta(502), resposta(dados={"artists": {"items": []}})])
    pausa = Mock()
    monkeypatch.setattr(api.requests, "request", chamada)
    monkeypatch.setattr(api.time, "sleep", pausa)
    resultado = api.requisitar("GET", "https://api.spotify.com/v1/search")
    assert resultado.status_code == 200
    assert chamada.call_count == 2
    pausa.assert_called_once_with(1)


def test_502_persistente_mostra_erro_claro(monkeypatch):
    chamada = Mock(return_value=resposta(502))
    pausa = Mock()
    monkeypatch.setattr(api.requests, "request", chamada)
    monkeypatch.setattr(api.time, "sleep", pausa)
    with pytest.raises(RuntimeError, match="Spotify está indisponível"):
        api.requisitar("GET", "https://api.spotify.com/v1/search")
    assert chamada.call_count == 4
    assert [c.args[0] for c in pausa.call_args_list] == [1, 2, 4]


def test_post_502_nao_e_repetido(monkeypatch):
    chamada = Mock(return_value=resposta(502))
    monkeypatch.setattr(api.requests, "request", chamada)
    assert api.requisitar("POST", "https://api.spotify.com/v1/me/playlists").status_code == 502
    assert chamada.call_count == 1


def test_quota_para_sem_repetir(monkeypatch):
    chamada = Mock(return_value=resposta(429, {"error": {"reason": "QUOTA_EXCEEDED"}}))
    monkeypatch.setattr(api.requests, "request", chamada)
    with pytest.raises(RuntimeError, match="Cota"):
        api.obter_perfil("token")
    assert chamada.call_count == 1


def test_quota_guarda_retry_after_e_bloqueia_novas_chamadas(monkeypatch):
    agora = [2000000000]
    monkeypatch.setattr(api.time, "time", lambda: agora[0])
    chamada = Mock(side_effect=[
        resposta(429, {"error": {"reason": "QUOTA_EXCEEDED"}}, {"Retry-After": "120"}),
        resposta(dados={"id": "usuario"}),
    ])
    monkeypatch.setattr(api.requests, "request", chamada)
    with pytest.raises(api.CotaSpotifyExcedida, match="Spotify pediu para aguardar"):
        api.obter_perfil("token")
    assert json.loads(api.COTA_CACHE.read_text())["ate"] == 2000000120
    with pytest.raises(api.CotaSpotifyExcedida):
        api.obter_perfil("token")
    assert chamada.call_count == 1
    agora[0] = 2000000121
    assert api.obter_perfil("token")["id"] == "usuario"
    assert chamada.call_count == 2


def test_client_id_ausente(ambiente, monkeypatch, capsys):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    assert main.main(["calmo"]) == 1
    assert "Preencha SPOTIFY_CLIENT_ID" in capsys.readouterr().err
