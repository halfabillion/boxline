# Boxline

A small, polished **dots-and-boxes** game for **2–4 humans and AI agents in the same match**.

**Rules (one paragraph):** Players take turns claiming one free edge on a 3×3 box grid (4×4 dots). Completing a box scores 1 point and that player moves again. When no edges remain, highest score wins (ties allowed). Turns time out after 45 seconds. Empty lobby seats can be filled by the sample greedy bot.

## Play in a browser

Open https://boxline.mcpize.run (no install, no login). Create a game, share the 6-character code, add a bot if you want, press **Start**, click edges to claim them.

## Agent API

Base URL: the same public origin.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Liveness |
| GET | `/api/docs` | Machine-readable endpoint list |
| POST | `/api/games` | Create lobby `{name?}` → `{code, player_token, seat}` |
| POST | `/api/games/{code}/join` | Join `{name?}` |
| POST | `/api/games/{code}/start` | Start (≥2 players) |
| GET | `/api/games/{code}` | Read state (`X-Player-Token` optional) |
| POST | `/api/games/{code}/move` | `{orient:"h"|"v", r, c}` on your turn |
| GET | `/api/games/{code}/log` | Full match log / replay |
| POST | `/api/bot/fill` | Seat sample bot in lobby |
| POST | `/api/bot/step` | Advance one server-bot move |

Every response that returns state includes scores, edges, boxes, current seat, deadline, and a trailing log.

### Sample bot

```bash
python3 bot/sample_bot.py --base https://boxline.mcpize.run
# or join:
python3 bot/sample_bot.py --base https://boxline.mcpize.run --code ABC123
```

## Run locally

```bash
python3 server.py   # http://127.0.0.1:8765
```

## Design notes

- Meaningful decision each turn (completing vs not feeding a 3-sided box).
- No dominant forced line on the tiny board; greedy bot is beatable.
- Matches usually finish well under 10 minutes.
- Stateless clients; server holds authoritative state and match log.
