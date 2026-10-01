import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from dotenv import load_dotenv

from app.spotify_api import requisitar

RAIZ = Path(__file__).resolve().parent.parent
CACHE = RAIZ / ".cache-spotify-token.json"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-modify-private playlist-modify-public user-library-read"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def _receber_codigo(url, state):
    resultado = {}

    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Não registrar códigos de autorização.

        def do_GET(self):
            partes = urlsplit(self.path)
            parametros = parse_qs(partes.query)
            recebido = parametros.get("state", [""])[0]
            if partes.path != "/callback":
                status, mensagem = 404, "Caminho desconhecido."
            elif not secrets.compare_digest(recebido, state):
                status, mensagem = 400, "Estado inválido. Continue o login na janela original."
            elif "error" in parametros:
                resultado["erro"] = "Login no Spotify recusado pelo usuário."
                status, mensagem = 400, resultado["erro"]
            elif parametros.get("code", [""])[0]:
                resultado["codigo"] = parametros["code"][0]
                status, mensagem = 200, "Login recebido. Você pode fechar esta janela."
            else:
                status, mensagem = 400, "Callback sem código de autorização."
            corpo = mensagem.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    try:
        servidor = HTTPServer(("127.0.0.1", 8888), Callback)
    except OSError as erro:
        raise RuntimeError("Não foi possível abrir 127.0.0.1:8888. Libere a porta e tente novamente.") from erro
    with servidor:
        servidor.timeout = 1
        if not webbrowser.open(url):
            raise RuntimeError("Não foi possível abrir o navegador para o login.")
        prazo = time.monotonic() + 180
        while not resultado and time.monotonic() < prazo:
            servidor.handle_request()
    if "erro" in resultado:
        raise RuntimeError(resultado["erro"])
    if "codigo" not in resultado:
        raise RuntimeError("Tempo de login esgotado. Execute novamente para tentar.")
    return resultado["codigo"]


def _login(client_id):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    url = "https://accounts.spotify.com/authorize?" + urlencode({
        "client_id": client_id, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "scope": SCOPES, "state": state,
        "code_challenge_method": "S256", "code_challenge": challenge,
    })
    codigo = _receber_codigo(url, state)
    resposta = requisitar("POST", TOKEN_URL, data={
        "grant_type": "authorization_code", "code": codigo,
        "redirect_uri": REDIRECT_URI, "client_id": client_id,
        "code_verifier": verifier,
    })
    resposta.raise_for_status()
    return resposta.json()


def _ler_cache(client_id):
    try:
        dados = json.loads(CACHE.read_text(encoding="utf-8"))
        if not isinstance(dados, dict):
            return {}
        if dados.get("client_id") != client_id or dados.get("scope") != SCOPES:
            return {}
        if not isinstance(dados.get("expires_at"), (int, float)):
            return {}
        return dados
    except (OSError, ValueError):
        return {}


def obter_token_valido():
    """Obtém um access token do cache, renovando ou abrindo o login se necessário."""
    load_dotenv(RAIZ / ".env")
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("Preencha SPOTIFY_CLIENT_ID no arquivo .env.")
    cache = _ler_cache(client_id)
    if cache.get("access_token") and cache["expires_at"] > time.time() + 60:
        return cache["access_token"]
    dados = None
    if cache.get("refresh_token"):
        resposta = requisitar("POST", TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": cache["refresh_token"],
            "client_id": client_id,
        })
        if resposta.status_code in (400, 401) and resposta.json().get("error") == "invalid_grant":
            CACHE.unlink(missing_ok=True)
            cache = {}
        else:
            resposta.raise_for_status()
            dados = resposta.json()
    if dados is None:
        dados = _login(client_id)
    salvo = {
        "client_id": client_id, "scope": SCOPES,
        "access_token": dados["access_token"],
        "refresh_token": dados.get("refresh_token") or cache.get("refresh_token"),
        "expires_at": time.time() + float(dados["expires_in"]),
    }
    temporario = CACHE.with_suffix(".cache")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(salvo, arquivo)
    temporario.replace(CACHE)
    return salvo["access_token"]
