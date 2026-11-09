#!/usr/bin/env python3
"""Boxline — turn-based multiplayer dots-and-boxes for humans + agents."""
from __future__ import annotations
import json, os, secrets, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

GRID = 3  # 3x3 boxes => 4x4 dots; short games
MAX_PLAYERS = 4
MIN_PLAYERS = 2
TURN_SECS = 45
LOCK = threading.RLock()
GAMES: dict[str, dict] = {}

def new_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))

def empty_edges(n: int):
    # horizontal edges: (n+1) rows of n edges; vertical: n rows of (n+1)
    return {
        "h": [[None for _ in range(n)] for _ in range(n + 1)],
        "v": [[None for _ in range(n + 1)] for _ in range(n)],
    }

def boxes_completed(edges, n, r, c, orient, player):
    """After claiming edge, return list of box coords completed by this move."""
    done = []
    h, v = edges["h"], edges["v"]
    def taken(x):
        return x is not None
    if orient == "h":
        if r > 0:
            if taken(h[r-1][c]) and taken(h[r][c]) and taken(v[r-1][c]) and taken(v[r-1][c+1]):
                done.append((r-1, c))
        if r < n:
            if taken(h[r][c]) and taken(h[r+1][c]) and taken(v[r][c]) and taken(v[r][c+1]):
                done.append((r, c))
    else:  # v
        if c > 0:
            if taken(v[r][c-1]) and taken(v[r][c]) and taken(h[r][c-1]) and taken(h[r+1][c-1]):
                done.append((r, c-1))
        if c < n:
            if taken(v[r][c]) and taken(v[r][c+1]) and taken(h[r][c]) and taken(h[r+1][c]):
                done.append((r, c))
    return done

def public_state(g: dict, seat: int | None = None) -> dict:
    return {
        "code": g["code"],
        "status": g["status"],  # lobby|active|finished
        "grid": g["grid"],
        "players": [{"seat": p["seat"], "name": p["name"], "score": p["score"], "connected": True} for p in g["players"]],
        "boxes": g["boxes"],
        "edges": g["edges"],
        "current_seat": g.get("current_seat"),
        "turn_deadline": g.get("turn_deadline"),
        "winner_seats": g.get("winner_seats"),
        "log": g["log"][-40:],
        "you": seat,
        "max_players": MAX_PLAYERS,
        "min_players": MIN_PLAYERS,
        "rules": "Dots and Boxes on a 3x3 box grid. Claim one free edge per turn. Completing a box scores 1 and you go again. Highest score wins when no edges remain. 2-4 players; empty seats can be filled by /api/bot/fill.",
    }

def ensure_turn_timeout(g):
    if g["status"] != "active":
        return
    dl = g.get("turn_deadline")
    if not dl or time.time() < dl:
        return
    # auto-skip: pass turn (no edge claimed)
    g["log"].append({"t": time.time(), "event": "timeout", "seat": g["current_seat"]})
    advance_turn(g)

def advance_turn(g, again=False):
    if again:
        g["turn_deadline"] = time.time() + TURN_SECS
        return
    n = len(g["players"])
    g["current_seat"] = (g["current_seat"] + 1) % n
    g["turn_deadline"] = time.time() + TURN_SECS

def edges_remaining(g):
    n = g["grid"]
    left = 0
    for row in g["edges"]["h"]:
        left += sum(1 for x in row if x is None)
    for row in g["edges"]["v"]:
        left += sum(1 for x in row if x is None)
    return left

def finish_if_done(g):
    if edges_remaining(g) > 0:
        return
    g["status"] = "finished"
    scores = [p["score"] for p in g["players"]]
    top = max(scores)
    g["winner_seats"] = [p["seat"] for p in g["players"] if p["score"] == top]
    g["log"].append({"t": time.time(), "event": "finished", "winners": g["winner_seats"], "scores": scores})

HTML = open(os.path.join(os.path.dirname(__file__), "static", "index.html"), encoding="utf-8").read() if os.path.exists(os.path.join(os.path.dirname(__file__), "static", "index.html")) else "<h1>Boxline</h1>"

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Player-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode() or "{}")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/api/health":
            return self._json(200, {"ok": True, "game": "boxline", "version": "1.0.0"})
        if u.path == "/api/docs":
            return self._json(200, {
                "name": "Boxline",
                "summary": "Dots-and-Boxes for 2-4 humans/agents. Claim edges; complete boxes to score and move again.",
                "endpoints": {
                    "POST /api/games": "body optional {name}. Create lobby. Returns code + player_token + seat.",
                    "POST /api/games/{code}/join": "body {name}. Join lobby.",
                    "POST /api/games/{code}/start": "Start when >=2 players (creator or any seated player).",
                    "GET /api/games/{code}": "Public state. Header X-Player-Token optional for you field.",
                    "POST /api/games/{code}/move": "body {orient:h|v, r:int, c:int}. Claim edge on your turn.",
                    "GET /api/games/{code}/log": "Full match log.",
                    "POST /api/bot/fill": "body {code, name?}. Seat a sample greedy bot in an empty lobby slot and auto-play.",
                    "POST /api/bot/step": "body {code}. Make one bot move if it is a bot seat's turn.",
                },
            })
        parts = u.path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "games":
            code = parts[2].upper()
            with LOCK:
                g = GAMES.get(code)
                if not g:
                    return self._json(404, {"error": "not_found"})
                ensure_turn_timeout(g)
                if len(parts) == 4 and parts[3] == "log":
                    return self._json(200, {"code": code, "log": g["log"]})
                token = self.headers.get("X-Player-Token")
                seat = None
                if token:
                    for p in g["players"]:
                        if p["token"] == token:
                            seat = p["seat"]
                            break
                return self._json(200, public_state(g, seat))
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        u = urlparse(self.path)
        data = self._read()
        parts = u.path.strip("/").split("/")

        if u.path == "/api/games":
            name = (data.get("name") or "Player").strip()[:24] or "Player"
            with LOCK:
                code = new_code()
                while code in GAMES:
                    code = new_code()
                token = secrets.token_hex(16)
                g = {
                    "code": code,
                    "status": "lobby",
                    "grid": GRID,
                    "players": [{"seat": 0, "name": name, "token": token, "score": 0, "bot": False}],
                    "edges": empty_edges(GRID),
                    "boxes": [[None for _ in range(GRID)] for _ in range(GRID)],
                    "current_seat": 0,
                    "turn_deadline": None,
                    "winner_seats": None,
                    "log": [{"t": time.time(), "event": "created", "by": name}],
                    "created": time.time(),
                }
                GAMES[code] = g
                return self._json(200, {"code": code, "player_token": token, "seat": 0, "state": public_state(g, 0)})

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "games":
            code = parts[2].upper()
            action = parts[3]
            with LOCK:
                g = GAMES.get(code)
                if not g:
                    return self._json(404, {"error": "not_found"})
                ensure_turn_timeout(g)
                token = self.headers.get("X-Player-Token") or data.get("player_token")

                if action == "join":
                    if g["status"] != "lobby":
                        return self._json(409, {"error": "not_lobby"})
                    if len(g["players"]) >= MAX_PLAYERS:
                        return self._json(409, {"error": "full"})
                    name = (data.get("name") or f"P{len(g['players'])}").strip()[:24]
                    tok = secrets.token_hex(16)
                    seat = len(g["players"])
                    g["players"].append({"seat": seat, "name": name, "token": tok, "score": 0, "bot": False})
                    g["log"].append({"t": time.time(), "event": "join", "seat": seat, "name": name})
                    return self._json(200, {"code": code, "player_token": tok, "seat": seat, "state": public_state(g, seat)})

                if action == "start":
                    if g["status"] != "lobby":
                        return self._json(409, {"error": "already_started"})
                    if len(g["players"]) < MIN_PLAYERS:
                        return self._json(409, {"error": "need_more_players", "have": len(g["players"]), "need": MIN_PLAYERS})
                    g["status"] = "active"
                    g["current_seat"] = 0
                    g["turn_deadline"] = time.time() + TURN_SECS
                    g["log"].append({"t": time.time(), "event": "start", "players": len(g["players"])})
                    return self._json(200, public_state(g))

                if action == "move":
                    if g["status"] != "active":
                        return self._json(409, {"error": "not_active", "status": g["status"]})
                    seat = None
                    for p in g["players"]:
                        if p["token"] == token:
                            seat = p["seat"]
                            break
                    if seat is None:
                        return self._json(401, {"error": "bad_token"})
                    if seat != g["current_seat"]:
                        return self._json(409, {"error": "not_your_turn", "current_seat": g["current_seat"]})
                    orient = data.get("orient")
                    try:
                        r = int(data["r"]); c = int(data["c"])
                    except Exception:
                        return self._json(400, {"error": "bad_coords"})
                    n = g["grid"]
                    if orient == "h":
                        if not (0 <= r <= n and 0 <= c < n):
                            return self._json(400, {"error": "out_of_range"})
                        if g["edges"]["h"][r][c] is not None:
                            return self._json(409, {"error": "taken"})
                        g["edges"]["h"][r][c] = seat
                    elif orient == "v":
                        if not (0 <= r < n and 0 <= c <= n):
                            return self._json(400, {"error": "out_of_range"})
                        if g["edges"]["v"][r][c] is not None:
                            return self._json(409, {"error": "taken"})
                        g["edges"]["v"][r][c] = seat
                    else:
                        return self._json(400, {"error": "bad_orient"})
                    completed = boxes_completed(g["edges"], n, r, c, orient, seat)
                    for (br, bc) in completed:
                        if g["boxes"][br][bc] is None:
                            g["boxes"][br][bc] = seat
                            g["players"][seat]["score"] += 1
                    g["log"].append({"t": time.time(), "event": "move", "seat": seat, "orient": orient, "r": r, "c": c, "completed": completed})
                    finish_if_done(g)
                    if g["status"] == "active":
                        advance_turn(g, again=bool(completed))
                    return self._json(200, public_state(g, seat))

        if u.path == "/api/bot/fill":
            code = (data.get("code") or "").upper()
            with LOCK:
                g = GAMES.get(code)
                if not g:
                    return self._json(404, {"error": "not_found"})
                if g["status"] != "lobby":
                    return self._json(409, {"error": "not_lobby"})
                if len(g["players"]) >= MAX_PLAYERS:
                    return self._json(409, {"error": "full"})
                name = (data.get("name") or "GreedyBot").strip()[:24]
                tok = secrets.token_hex(16)
                seat = len(g["players"])
                g["players"].append({"seat": seat, "name": name, "token": tok, "score": 0, "bot": True})
                g["log"].append({"t": time.time(), "event": "bot_fill", "seat": seat, "name": name})
                return self._json(200, {"seat": seat, "player_token": tok, "state": public_state(g)})

        if u.path == "/api/bot/step":
            code = (data.get("code") or "").upper()
            with LOCK:
                g = GAMES.get(code)
                if not g:
                    return self._json(404, {"error": "not_found"})
                ensure_turn_timeout(g)
                if g["status"] != "active":
                    return self._json(409, {"error": "not_active"})
                seat = g["current_seat"]
                pl = g["players"][seat]
                if not pl.get("bot"):
                    return self._json(409, {"error": "not_bot_turn", "current_seat": seat})
                move = choose_bot_move(g)
                if not move:
                    return self._json(409, {"error": "no_moves"})
                orient, r, c = move
                # reuse move logic via internal call
                n = g["grid"]
                if orient == "h":
                    g["edges"]["h"][r][c] = seat
                else:
                    g["edges"]["v"][r][c] = seat
                completed = boxes_completed(g["edges"], n, r, c, orient, seat)
                for (br, bc) in completed:
                    if g["boxes"][br][bc] is None:
                        g["boxes"][br][bc] = seat
                        pl["score"] += 1
                g["log"].append({"t": time.time(), "event": "bot_move", "seat": seat, "orient": orient, "r": r, "c": c, "completed": completed})
                finish_if_done(g)
                if g["status"] == "active":
                    advance_turn(g, again=bool(completed))
                return self._json(200, public_state(g))

        return self._json(404, {"error": "not_found"})

def choose_bot_move(g):
    n = g["grid"]
    # Prefer completing a box; else avoid giving a 3-sided box; else random free edge
    candidates = []
    for r in range(n + 1):
        for c in range(n):
            if g["edges"]["h"][r][c] is None:
                candidates.append(("h", r, c))
    for r in range(n):
        for c in range(n + 1):
            if g["edges"]["v"][r][c] is None:
                candidates.append(("v", r, c))
    # try completing
    for orient, r, c in candidates:
        # simulate
        edges = {"h": [row[:] for row in g["edges"]["h"]], "v": [row[:] for row in g["edges"]["v"]]}
        if orient == "h":
            edges["h"][r][c] = g["current_seat"]
        else:
            edges["v"][r][c] = g["current_seat"]
        if boxes_completed(edges, n, r, c, orient, g["current_seat"]):
            return (orient, r, c)
    # avoid creating 3-edge boxes for opponent when possible
    safe = []
    for orient, r, c in candidates:
        edges = {"h": [row[:] for row in g["edges"]["h"]], "v": [row[:] for row in g["edges"]["v"]]}
        if orient == "h":
            edges["h"][r][c] = g["current_seat"]
        else:
            edges["v"][r][c] = g["current_seat"]
        # count boxes with 3 edges
        danger = 0
        for br in range(n):
            for bc in range(n):
                cnt = sum([
                    edges["h"][br][bc] is not None,
                    edges["h"][br+1][bc] is not None,
                    edges["v"][br][bc] is not None,
                    edges["v"][br][bc+1] is not None,
                ])
                if cnt == 3:
                    danger += 1
        if danger == 0:
            safe.append((orient, r, c))
    pool = safe or candidates
    return pool[0] if pool else None

def main():
    port = int(os.environ.get("PORT", "8765"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"boxline listening on {port}", flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
