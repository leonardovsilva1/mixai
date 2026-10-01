# Projeto: gerador de playlists por mood

App de terminal em Python: o usuário descreve um mood, o Gemini sugere músicas, o app valida cada uma no Spotify e cria uma playlist na conta do usuário, devolvendo o link.

## Stack
- Python 3.11+
- Spotify: chamadas HTTP diretas com `requests`. NÃO usar spotipy nem outras bibliotecas de Spotify.
- IA: SDK oficial `google-genai` (`from google import genai`). Toda chamada à IA fica isolada em `app/ia.py`, para que o modelo possa ser trocado depois.
- Testes com pytest.

## Regras da API do Spotify (mudaram em 2026, não confie em tutoriais antigos)
- Autenticação: Authorization Code com PKCE, sem client secret. Redirect URI: http://127.0.0.1:8888/callback
- Criar playlist: POST /me/playlists (o endpoint /users/{id}/playlists foi removido).
- Adicionar músicas: POST /playlists/{id}/items (o endpoint /playlists/{id}/tracks foi removido). Máximo de 100 URIs por requisição.
- Busca: GET /search com limit máximo de 10.
- Verificar se o usuário já salvou algo: GET /me/library/contains.
- Não usar: recommendations, audio-features, related-artists, artist top-tracks, browse. Foram removidos ou restritos.
- Tratar erro 429 respeitando o header Retry-After. Se o corpo tiver "reason": "QUOTA_EXCEEDED", avisar o usuário e parar.
- Nunca enviar dados vindos do Spotify para o modelo de IA (os termos do Spotify proíbem).

## Segurança
- Chaves e tokens só no arquivo .env e em arquivos de cache listados no .gitignore. Nunca no código.

## Estilo
- Código e comentários simples, nomes em português quando fizer sentido.
- Mudanças pequenas e focadas no que foi pedido.
