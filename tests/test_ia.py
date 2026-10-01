import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from google.genai import errors

import main
from app import ia


def musica(**alteracoes):
    return {"artista": "Artista teste", "titulo": "Canção teste", "ano": 1995,
            "nota": "Uma canção suave para relaxar.", **alteracoes}


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(ia, "load_dotenv", lambda *args: None)
    monkeypatch.setenv("GEMINI_API_KEY", "chave-ficticia")
    monkeypatch.setenv("GEMINI_MODEL", "modelo-teste")
    fabrica = MagicMock()
    falso = fabrica.return_value.__enter__.return_value
    falso.models.generate_content.return_value = SimpleNamespace(text=json.dumps([musica()]))
    monkeypatch.setattr(ia.genai, "Client", fabrica)
    return falso


def test_json_valido():
    assert ia.validar_sugestoes(json.dumps([musica(artista=" Artista teste ")]), 1) == [musica()]


@pytest.mark.parametrize("texto", [None, "", "{", "```json\n[]\n```", "null", "{}", "[]"])
def test_json_invalido(texto):
    with pytest.raises(ValueError):
        ia.validar_sugestoes(texto, 1)


@pytest.mark.parametrize("item", [
    None, "musica", {}, {"artista": "Teste"}, musica(extra="não permitido"),
    musica(artista=" "), musica(titulo=None), musica(nota=12), musica(nota=""),
    musica(ano="1995"), musica(ano=True), musica(ano=1995.5), musica(ano=0),
    musica(ano=date.today().year + 1), musica(nota="a" * 241), musica(nota="a\nb"),
])
def test_campos_invalidos(item):
    with pytest.raises(ValueError):
        ia.validar_sugestoes(json.dumps([item]), 1)


def test_quantidade_incorreta():
    with pytest.raises(ValueError, match="2 músicas"):
        ia.validar_sugestoes(json.dumps([musica()]), 2)


def test_duplicada():
    with pytest.raises(ValueError, match="repetidas"):
        ia.validar_sugestoes(json.dumps([musica(), musica(artista=" ARTISTA TESTE ", titulo="canção teste")]), 2)


def test_lote_com_quatro_faixas_mesmo_artista_e_validado():
    itens = [musica(titulo=f"Faixa {i}", artista=" ARTISTA teste " if i == 3 else "Artista teste") for i in range(4)]
    assert len(ia.validar_sugestoes(json.dumps(itens), 4)) == 4


def test_sdk_com_schema_e_modelo_configurado(cliente):
    assert ia.sugerir_musicas("calmo anos 90", 1) == [musica()]
    argumentos = cliente.models.generate_content.call_args.kwargs
    assert argumentos["model"] == "modelo-teste"
    assert json.loads(argumentos["contents"]) == {"mood": "calmo anos 90", "quantidade": 1}
    config = argumentos["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["items"]["required"] == ["artista", "titulo", "ano", "nota"]
    assert config.response_json_schema["minItems"] == config.response_json_schema["maxItems"] == 1


def test_resposta_sdk_invalida(cliente):
    cliente.models.generate_content.return_value = SimpleNamespace(text="não é json")
    with pytest.raises(RuntimeError, match="Resposta inválida"):
        ia.sugerir_musicas("calmo", 1)


def test_titulos_anteriores_no_prompt(cliente):
    ia.sugerir_musicas("calmo", 1, ["Anterior"])
    conteudo = json.loads(cliente.models.generate_content.call_args.kwargs["contents"])
    assert conteudo == {"mood": "calmo", "quantidade": 1, "titulos_ja_sugeridos": ["Anterior"]}


def test_artistas_pedidos_orientam_sugestoes(cliente):
    ia.sugerir_musicas("trap hard e 808 alto", 1, artistas_pedidos=["PHL Notunrboy"])
    argumentos = cliente.models.generate_content.call_args.kwargs
    assert json.loads(argumentos["contents"]) == {
        "mood": "trap hard e 808 alto", "quantidade": 1,
        "artistas_pedidos": ["PHL Notunrboy"],
    }
    assert "mesma prioridade dos artistas" in argumentos["config"].system_instruction


def test_faixa_de_referencia_nao_restringe_artistas(cliente):
    referencia = {"titulo": "Goth", "artista": "Sidewalks and Skeletons"}
    contexto = {"generos": ["witch house"], "artistas_proximos": ["Salem"]}
    ia.sugerir_musicas("parecidas com Goth", 1, referencia=referencia,
                       contexto_referencia=contexto)
    argumentos = cliente.models.generate_content.call_args.kwargs
    conteudo = json.loads(argumentos["contents"])
    assert conteudo["faixa_referencia"] == referencia
    assert conteudo["contexto_da_referencia"] == contexto
    assert "principalmente de outros artistas" in argumentos["config"].system_instruction


@pytest.mark.parametrize("codigo, mensagem", [(429, "Limite de uso"), (403, "Acesso"), (503, "indisponível"), (400, "recusou")])
def test_erro_api(cliente, codigo, mensagem):
    cliente.models.generate_content.side_effect = errors.APIError(codigo, {"error": {"message": "erro simulado"}})
    with pytest.raises(RuntimeError, match=mensagem):
        ia.sugerir_musicas("calmo", 1)


@pytest.mark.parametrize("erro", [httpx.ConnectError("offline"), httpx.ReadTimeout("timeout"), OSError("offline")])
def test_erro_rede(cliente, erro):
    cliente.models.generate_content.side_effect = erro
    with pytest.raises(RuntimeError, match="Falha de rede"):
        ia.sugerir_musicas("calmo", 1)


@pytest.mark.parametrize("campo", ["GEMINI_API_KEY", "GEMINI_MODEL"])
def test_configuracao_ausente(cliente, monkeypatch, campo):
    monkeypatch.delenv(campo)
    with pytest.raises(RuntimeError, match="Preencha"):
        ia.sugerir_musicas("calmo", 1)
    cliente.models.generate_content.assert_not_called()


@pytest.mark.parametrize("mood, quantidade", [("", 1), (" ", 1), (None, 1), ("calmo", 0), ("calmo", -1), ("calmo", True), ("calmo", 1.5)])
def test_entrada_invalida(cliente, mood, quantidade):
    with pytest.raises(ValueError):
        ia.sugerir_musicas(mood, quantidade)
    cliente.models.generate_content.assert_not_called()
