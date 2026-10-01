# Gerador de playlists por mood

Plataforma web local em Python 3.11+ que transforma um mood em uma playlist privada no Spotify. O Gemini sugere faixas; o Spotify confirma artista e título.

## Preparação

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

No Linux/macOS, ative com `source .venv/bin/activate` e copie o exemplo com `cp .env.example .env`.

Preencha no `.env`:

- `SPOTIFY_CLIENT_ID`: ID do seu aplicativo Spotify.
- `GEMINI_API_KEY`: chave da API do Gemini.
- `GEMINI_MODEL`: identificador de um modelo com suporte a JSON estruturado.

No aplicativo do Spotify Developer Dashboard, cadastre exatamente `http://127.0.0.1:8888/callback`. Não é necessário client secret. No primeiro uso, autorize o login no navegador. Os tokens ficam em `.cache-spotify-token.json`, ignorado pelo Git, e são renovados automaticamente.

## Plataforma web

Inicie o servidor local:

```powershell
python -m app.web
```

Abra `http://127.0.0.1:5000`. Descreva o mood e escolha a quantidade. A MixAI mostra uma prévia para você remover ou trocar faixas antes de confirmar a criação da playlist. As alternativas dependem das faixas encontradas, e a prévia expira após 30 minutos ou ao reiniciar o servidor. No primeiro uso, o Spotify abre no navegador para autorização. A interface usa a paleta e o símbolo da logo MixAI.

O servidor escuta apenas em `127.0.0.1`, pois a autenticação e o cache de tokens atuais pertencem ao usuário local desta máquina. Para publicar em um servidor e atender várias contas, será necessário implementar OAuth por sessão web antes da implantação.

## Uso pelo terminal

```powershell
python main.py "trap hard de 2026, estilo Phl Notunrboy e Guap508" --tamanho 20
```

O tamanho padrão é 20. O app pede cerca de 1,5 vez a quantidade desejada ao Gemini, valida no Spotify e mantém as primeiras músicas aceitas, sem duplicatas e com até 3 por artista. Se faltar, faz uma segunda e última rodada, pedindo cerca de 1,5 vez a quantidade restante. Apenas os títulos previamente sugeridos pela própria IA voltam ao Gemini; nenhum dado do Spotify é enviado ao modelo.

O nome da playlist vem do mood, abreviado para até 60 caracteres. A descrição preserva o mood inteiro, por isso a entrada aceita até 300 caracteres. A playlist é privada e recebe lotes de até 100 URIs. Ao concluir, o terminal mostra artista, título e link.

Se houver menos músicas válidas após duas rodadas, o app avisa e cria uma playlist menor. Se não houver nenhuma, não cria playlist. Em caso de falha após a criação, mostra o link da playlist possivelmente vazia ou parcial, sem recriá-la automaticamente.

A validação confirma semelhança de título e artista no catálogo; não confirma ano original nem clima musical. Os modos temporários de linha de comando foram removidos. Os módulos de planejamento e busca direta continuam disponíveis internamente.

## Testes

```powershell
python -m pytest -q
```

Os testes simulam Gemini e Spotify, sem criar playlists nem usar credenciais reais. Os testes de autenticação usam um callback HTTP local temporário.
