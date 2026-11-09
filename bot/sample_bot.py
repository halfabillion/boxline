#!/usr/bin/env python3
"""Minimal Boxline agent: join/create, fill if needed, play greedy moves via HTTP JSON."""
import argparse, json, time, urllib.request

def call(base, method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Player-Token", token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="Public game base URL, no trailing slash")
    ap.add_argument("--code", help="Join existing code; else create")
    ap.add_argument("--name", default="SampleBot")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    if args.code:
        j = call(base, "POST", f"/api/games/{args.code.upper()}/join", {"name": args.name})
    else:
        j = call(base, "POST", "/api/games", {"name": args.name})
    code, token, seat = j["code"], j["player_token"], j["seat"]
    print(json.dumps({"joined": code, "seat": seat}))
    # ensure opponent
    st = call(base, "GET", f"/api/games/{code}", token=token)
    if st["status"] == "lobby" and len(st["players"]) < 2:
        call(base, "POST", "/api/bot/fill", {"code": code, "name": "GreedyBot"})
    st = call(base, "GET", f"/api/games/{code}", token=token)
    if st["status"] == "lobby":
        call(base, "POST", f"/api/games/{code}/start", {}, token=token)
    while True:
        st = call(base, "GET", f"/api/games/{code}", token=token)
        if st["status"] == "finished":
            print(json.dumps({"finished": True, "winners": st.get("winner_seats"), "scores": [p["score"] for p in st["players"]]}))
            break
        if st["status"] == "active" and st.get("current_seat") == seat:
            # ask server bot chooser indirectly: enumerate first free edge
            move = None
            n = st["grid"]
            for r in range(n + 1):
                for c in range(n):
                    if st["edges"]["h"][r][c] is None:
                        move = {"orient": "h", "r": r, "c": c}
                        break
                if move: break
            if not move:
                for r in range(n):
                    for c in range(n + 1):
                        if st["edges"]["v"][r][c] is None:
                            move = {"orient": "v", "r": r, "c": c}
                            break
                    if move: break
            if move:
                st = call(base, "POST", f"/api/games/{code}/move", move, token=token)
                print(json.dumps({"moved": move, "score": st["players"][seat]["score"]}))
        else:
            # nudge server bots
            try:
                call(base, "POST", "/api/bot/step", {"code": code})
            except Exception:
                pass
        time.sleep(0.4)

if __name__ == "__main__":
    main()
