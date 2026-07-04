#!/usr/bin/env python3
"""Headless tests for the multiplayer Snake engine and WebSocket plumbing.

Standard library only — run with::

    python3 test_multiplayer.py

Covers the network-free game engine (movement, food, collisions, respawn,
snapshot) plus the RFC 6455 handshake/frame codec, and finishes with a live
end-to-end WebSocket round-trip against a real server instance.
"""

import asyncio
import json
import random
import struct
import sys

import multiplayer as mp


passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def seeded_game():
    # Deterministic RNG so spawns/food are reproducible across runs.
    return mp.MultiplayerSnake(width=20, height=15, rng=random.Random(1234))


def test_add_and_spawn():
    print("add_player / spawn")
    g = seeded_game()
    p = g.add_player("Alice")
    check("player is alive after join", p.alive)
    check("snake starts at START_LENGTH", len(p.body) == mp.START_LENGTH)
    check("player got a colour", p.color.startswith("#"))
    check("food was placed", len(g.food) >= mp.BASE_FOOD)
    p2 = g.add_player()
    check("second player gets default name", p2.name == "Player 2")
    check("ids are unique", p.id != p2.id)


def test_movement():
    print("movement")
    g = seeded_game()
    p = g.add_player("Mover")
    # Make sure nothing is in the way and no food gets eaten.
    g.food.clear()
    p.body = mp.deque([(5, 5), (4, 5), (3, 5)])
    p.direction = mp.DIRECTIONS["right"]
    p.pending_direction = mp.DIRECTIONS["right"]
    g.tick()
    check("head advanced right", p.head == (6, 5))
    check("length unchanged when not eating", len(p.body) == 3)
    check("tail followed", (3, 5) not in p.body)


def test_reversal_blocked():
    print("reversal blocked")
    g = seeded_game()
    p = g.add_player()
    p.body = mp.deque([(5, 5), (4, 5), (3, 5)])
    p.direction = mp.DIRECTIONS["right"]
    p.set_direction("left")  # 180 into the neck -> must be ignored
    check("reversal ignored", p.pending_direction == mp.DIRECTIONS["right"])
    p.set_direction("up")
    check("perpendicular turn accepted", p.pending_direction == mp.DIRECTIONS["up"])


def test_eat_grows_and_scores():
    print("eating grows + scores")
    g = seeded_game()
    p = g.add_player()
    g.food = {(6, 5)}
    p.body = mp.deque([(5, 5), (4, 5), (3, 5)])
    p.direction = mp.DIRECTIONS["right"]
    p.pending_direction = mp.DIRECTIONS["right"]
    g.tick()
    check("score incremented", p.score == 1)
    check("snake grew by one", len(p.body) == 4)
    check("food consumed", (6, 5) not in g.food)
    check("food replenished to target", len(g.food) >= g._food_target())


def test_wall_collision_kills_and_respawns():
    print("wall collision -> death -> respawn")
    g = seeded_game()
    p = g.add_player()
    g.food.clear()
    # Head one step from the right wall, moving into it.
    p.body = mp.deque([(g.width - 1, 5), (g.width - 2, 5), (g.width - 3, 5)])
    p.direction = mp.DIRECTIONS["right"]
    p.pending_direction = mp.DIRECTIONS["right"]
    g.tick()
    check("player died on wall", not p.alive)
    check("respawn scheduled", p.respawn_at > g.tick_count)
    # Fast-forward past the respawn timer.
    for _ in range(mp.RESPAWN_TICKS + 1):
        g.tick()
    check("player respawned alive", p.alive)
    check("respawn resets score", p.score == 0)


def test_head_to_head_kills_both():
    print("head-to-head collision")
    g = seeded_game()
    a = g.add_player("A")
    b = g.add_player("B")
    g.food.clear()
    # Aim both heads at the same empty cell (10, 5).
    a.body = mp.deque([(9, 5), (8, 5), (7, 5)])
    a.direction = a.pending_direction = mp.DIRECTIONS["right"]
    b.body = mp.deque([(11, 5), (12, 5), (13, 5)])
    b.direction = b.pending_direction = mp.DIRECTIONS["left"]
    g.tick()
    check("both snakes died", (not a.alive) and (not b.alive))


def test_body_collision_kills():
    print("running into another body")
    g = seeded_game()
    a = g.add_player("A")
    b = g.add_player("B")
    g.food.clear()
    # B's head will move into A's stationary-ish long body.
    a.body = mp.deque([(10, 5), (10, 6), (10, 7), (10, 8)])
    a.direction = a.pending_direction = mp.DIRECTIONS["up"]  # moves to (10,4)
    b.body = mp.deque([(9, 6), (8, 6), (7, 6)])
    b.direction = b.pending_direction = mp.DIRECTIONS["right"]  # into (10,6) = A's body
    g.tick()
    check("B died hitting A's body", not b.alive)
    check("A survived", a.alive)


def test_snapshot_shape():
    print("snapshot shape")
    g = seeded_game()
    g.add_player("Snap")
    g.tick()
    snap = g.snapshot()
    check("snapshot is state type", snap["type"] == "state")
    check("has board dims", snap["w"] == 20 and snap["h"] == 15)
    check("snakes present", len(snap["snakes"]) == 1)
    s = snap["snakes"][0]
    check("body is list of pairs", all(len(c) == 2 for c in s["body"]))
    # Must be JSON serialisable end-to-end.
    check("json round-trips", json.loads(json.dumps(snap))["type"] == "state")


def test_remove_player():
    print("remove_player")
    g = seeded_game()
    p = g.add_player()
    g.remove_player(p.id)
    check("player removed", p.id not in g.players)
    check("snapshot has no snakes", len(g.snapshot()["snakes"]) == 0)


def test_ws_accept_key():
    print("websocket accept key (RFC 6455 example)")
    # The canonical example from the RFC.
    accept = mp.compute_accept("dGhlIHNhbXBsZSBub25jZQ==")
    check("accept matches spec", accept == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


def test_frame_roundtrip():
    print("frame encode -> masked decode")
    payload = json.dumps({"type": "dir", "dir": "left"})

    # Build a *masked* client frame (as a browser would send) and feed it
    # through read_frame via an in-memory StreamReader.
    async def run():
        raw = payload.encode("utf-8")
        mask = b"\x21\x09\x77\x40"
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(raw))
        header = bytes([0x80 | mp.OP_TEXT, 0x80 | len(raw)]) + mask
        reader = asyncio.StreamReader()
        reader.feed_data(header + masked)
        reader.feed_eof()
        opcode, data = await mp.read_frame(reader)
        return opcode, data

    opcode, data = asyncio.run(run())
    check("opcode is text", opcode == mp.OP_TEXT)
    check("payload unmasked correctly", data.decode("utf-8") == payload)


def test_encode_frame_lengths():
    print("frame length framing")
    small = mp.encode_frame("hi")
    check("small frame len byte", small[1] == 2)
    medium = mp.encode_frame("x" * 200)
    check("medium uses 126 marker", medium[1] == 126)
    length = struct.unpack(">H", medium[2:4])[0]
    check("medium length correct", length == 200)


def test_live_roundtrip():
    print("live server end-to-end round-trip")

    async def run():
        server = mp.GameServer(host="127.0.0.1", port=0)
        srv = await asyncio.start_server(server.handle_connection, "127.0.0.1", 0)
        loop = asyncio.get_event_loop()
        loop.create_task(server.game_loop())
        port = srv.sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        # Minimal client handshake.
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        writer.write((
            f"GET /ws HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        await writer.drain()

        resp = await reader.readuntil(b"\r\n\r\n")
        assert b"101" in resp, resp
        assert mp.compute_accept(key).encode() in resp

        # Client must mask its frames; helper to send masked text.
        def send_client(text):
            raw = text.encode("utf-8")
            mask = b"\x01\x02\x03\x04"
            masked = bytes(b ^ mask[i & 3] for i, b in enumerate(raw))
            writer.write(bytes([0x80 | mp.OP_TEXT, 0x80 | len(raw)]) + mask + masked)

        # First two server frames: welcome, then an initial state snapshot.
        op1, d1 = await mp.read_frame(reader)
        welcome = json.loads(d1)
        op2, d2 = await mp.read_frame(reader)
        first_state = json.loads(d2)

        # Drive the snake straight up for a few ticks and confirm it moves.
        send_client(json.dumps({"type": "setname", "name": "Tester"}))
        send_client(json.dumps({"type": "dir", "dir": "up"}))
        await writer.drain()

        head_before = None
        head_after = None
        for _ in range(6):
            op, d = await mp.read_frame(reader)
            st = json.loads(d)
            if st.get("type") != "state":
                continue
            me = next((s for s in st["snakes"] if s["id"] == welcome["id"]), None)
            if me and me["body"]:
                if head_before is None:
                    head_before = me["body"][0]
                head_after = me["body"][0]
                name_seen = me["name"]

        writer.close()
        srv.close()
        await srv.wait_closed()
        return welcome, first_state, head_before, head_after, name_seen

    welcome, first_state, head_before, head_after, name_seen = asyncio.run(run())
    check("welcome carries id", isinstance(welcome.get("id"), int))
    check("welcome carries board size", welcome.get("w") == mp.BOARD_WIDTH)
    check("initial state received", first_state.get("type") == "state")
    check("setname applied", name_seen == "Tester")
    check("snake moved across ticks", head_before != head_after)


def main():
    tests = [
        test_add_and_spawn,
        test_movement,
        test_reversal_blocked,
        test_eat_grows_and_scores,
        test_wall_collision_kills_and_respawns,
        test_head_to_head_kills_both,
        test_body_collision_kills,
        test_snapshot_shape,
        test_remove_player,
        test_ws_accept_key,
        test_frame_roundtrip,
        test_encode_frame_lengths,
        test_live_roundtrip,
    ]
    for t in tests:
        t()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
