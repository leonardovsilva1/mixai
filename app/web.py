import secrets
import threading
import time

from flask import Flask, jsonify, render_template, request
import requests

from main import _publicar_playlist, selecionar_musicas_por_mood
from app.spotify_api import CotaSpotifyExcedida

app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024
PREVIAS = {}
TRAVA_PREVIAS = threading.Lock()
VALIDADE_PREVIA = 30 * 60


@app.get("/")
def inicio():
    return render_template("index.html")


def _falha(erro):
    return jsonify(erro=str(erro)), 429 if isinstance(erro, CotaSpotifyExcedida) else 502


def _limpar_previas():
    agora = time.monotonic()
    for codigo, previa in list(PREVIAS.items()):
        if agora - previa["criada_em"] > VALIDADE_PREVIA:
            del PREVIAS[codigo]


@app.post("/api/previews")
def criar_previa():
    dados = request.get_json(silent=True) or {}
    mood = dados.get("mood")
    tamanho = dados.get("tamanho", 20)
    if not isinstance(mood, str) or not mood.strip() or len(mood) > 300:
        return jsonify(erro="Descreva seu mood em até 300 caracteres."), 400
    if type(tamanho) is not int or not 1 <= tamanho <= 100:
        return jsonify(erro="Escolha de 1 a 100 músicas."), 400
    try:
        candidatas = selecionar_musicas_por_mood(
            mood.strip(), tamanho, exigir_quantidade=False, reservas=5,
        )
    except (RuntimeError, requests.RequestException, OSError, ValueError) as erro:
        return _falha(erro)
    if not candidatas:
        return jsonify(erro="Nenhuma música foi encontrada para a prévia."), 422
    codigo = secrets.token_urlsafe(24)
    faixas = {secrets.token_urlsafe(12): musica for musica in candidatas}
    with TRAVA_PREVIAS:
        _limpar_previas()
        PREVIAS[codigo] = {
            "mood": mood.strip(), "tamanho": tamanho,
            "faixas": faixas, "criada_em": time.monotonic(),
        }
    lista = [
        {"id": identificador, "artista": musica["artista"], "titulo": musica["titulo"]}
        for identificador, musica in faixas.items()
    ]
    return jsonify({
        "previa": codigo,
        "musicas": lista[:tamanho],
        "alternativas": lista[tamanho:],
        "quantidade_pedida": tamanho,
    })


@app.post("/api/playlists")
def criar():
    dados = request.get_json(silent=True) or {}
    codigo = dados.get("previa")
    ids = dados.get("faixas")
    if not isinstance(codigo, str) or not codigo:
        return jsonify(erro="Crie uma prévia antes de salvar a playlist."), 400
    with TRAVA_PREVIAS:
        _limpar_previas()
        previa = PREVIAS.get(codigo)
        if previa is None:
            return jsonify(erro="A prévia expirou. Gere uma nova seleção."), 410
        if (not isinstance(ids, list) or not 1 <= len(ids) <= previa["tamanho"]
            or any(not isinstance(i, str) or i not in previa["faixas"] for i in ids)
            or len(set(ids)) != len(ids)):
            return jsonify(erro="Escolha músicas válidas da prévia, sem repetições."), 400
        del PREVIAS[codigo]
    try:
        resultado = _publicar_playlist(previa["mood"], [previa["faixas"][i] for i in ids])
    except (RuntimeError, requests.RequestException, OSError, ValueError) as erro:
        return _falha(erro)
    return jsonify({
        "nome": resultado["nome"],
        "link": resultado["link"],
        "quantidade": resultado["quantidade"],
        "musicas": [{"artista": m["artista"], "titulo": m["titulo"]}
                    for m in resultado["musicas"]],
    })


def main():
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
