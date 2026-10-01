import json
import math
import time
from datetime import datetime
from pathlib import Path

import requests


class CotaSpotifyExcedida(RuntimeError):
    pass


COTA_CACHE = Path(__file__).resolve().parent.parent / ".cache-spotify-cota.json"


def _mensagem_cota(ate=None):
    mensagem = "Cota de uso da API do Spotify excedida."
    if ate and ate > time.time():
        horario = datetime.fromtimestamp(ate).astimezone().strftime("%d/%m às %H:%M")
        return f"{mensagem} O Spotify pediu para aguardar até {horario}."
    return f"{mensagem} Aguarde a liberação da cota antes de tentar novamente."


def verificar_cota_spotify():
    """Evita novas buscas enquanto o Retry-After da cota não terminou."""
    try:
        ate = float(json.loads(COTA_CACHE.read_text(encoding="utf-8"))["ate"])
    except (OSError, ValueError, TypeError, KeyError):
        return
    if math.isfinite(ate) and ate > time.time():
        raise CotaSpotifyExcedida(_mensagem_cota(ate))


def requisitar(method, url, **kwargs):
    """Respeita limites e repete leituras após falhas temporárias."""
    if url.startswith("https://api.spotify.com/"):
        verificar_cota_spotify()
    for tentativa in range(4):
        try:
            resposta = requests.request(method, url, timeout=30, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if method.upper() != "GET" or tentativa == 3:
                raise
            time.sleep(2 ** tentativa)
            continue
        try:
            corpo = resposta.json()
        except ValueError:
            corpo = {}
        erro = corpo.get("error", {}) if isinstance(corpo, dict) else {}
        reason = corpo.get("reason") if isinstance(corpo, dict) else None
        if reason == "QUOTA_EXCEEDED" or (
            isinstance(erro, dict) and erro.get("reason") == "QUOTA_EXCEEDED"
        ):
            try:
                espera = max(0, float(resposta.headers.get("Retry-After", "0")))
            except ValueError:
                espera = 0
            ate = time.time() + espera if math.isfinite(espera) and espera > 0 else None
            if ate:
                try:
                    COTA_CACHE.write_text(json.dumps({"ate": ate}), encoding="utf-8")
                except OSError:
                    pass
            raise CotaSpotifyExcedida(_mensagem_cota(ate))
        if method.upper() == "GET" and resposta.status_code in (502, 503, 504):
            if tentativa == 3:
                raise RuntimeError("O Spotify está indisponível no momento. Tente novamente mais tarde.")
            time.sleep(2 ** tentativa)
            continue
        if resposta.status_code != 429:
            return resposta
        if tentativa == 3:
            raise RuntimeError("Limite temporário do Spotify atingido. Tente mais tarde.")
        try:
            espera = max(0, float(resposta.headers.get("Retry-After", "1")))
        except ValueError:
            espera = 1
        time.sleep(espera)


def obter_perfil(token):
    resposta = requisitar(
        "GET", "https://api.spotify.com/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    resposta.raise_for_status()
    return resposta.json()


def criar_playlist(token, nome, descricao):
    resposta = requisitar(
        "POST", "https://api.spotify.com/v1/me/playlists",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": nome, "description": descricao, "public": False},
    )
    resposta.raise_for_status()
    return resposta.json()


def adicionar_musicas(token, playlist_id, uris):
    for inicio in range(0, len(uris), 100):
        resposta = requisitar(
            "POST", f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
            headers={"Authorization": f"Bearer {token}"},
            json={"uris": uris[inicio:inicio + 100]},
        )
        resposta.raise_for_status()
