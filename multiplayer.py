#!/usr/bin/env python3
"""Realtime multiplayer Snake, served over WebSockets.

Standard library only (no ``pip install`` needed) — the WebSocket protocol
(RFC 6455 handshake + frame codec) is implemented directly on top of
``asyncio``. One process both serves the browser client and runs an
authoritative game loop, so every player sees the same board in realtime.

Run it::

    python3 multiplayer.py            # listens on http://0.0.0.0:8765
    python3 multiplayer.py --port 9000
    PORT=9000 python3 multiplayer.py  # env var also works

then open http://localhost:8765 in one or more browser tabs (or share your LAN
address with friends on the same network). Steer with the arrow keys or WASD.

Design in a nutshell:
    * ``MultiplayerSnake`` is a pure, network-free game engine. It owns the
      shared board, every player's snake, the food, collisions, scoring and
      respawns, and advances one tick at a time. It is fully unit-testable
      without any sockets.
    * ``websocket`` helpers implement just enough of RFC 6455 to accept a
      browser upgrade and exchange text frames.
    * ``GameServer`` wires the two together: it accepts connections, serves the
      embedded HTML/JS client for plain HTTP GETs, upgrades ``/ws`` requests to
      WebSockets, and runs the tick loop that broadcasts state to everyone.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import random
import struct
from collections import deque

# --- Board / gameplay tuning ----------------------------------------------

BOARD_WIDTH = 40
BOARD_HEIGHT = 30
# Game speed. The loop advances the whole world this many times per second and
# broadcasts the new state to every connected client after each tick.
TICKS_PER_SECOND = 9
START_LENGTH = 3
# Ticks a dead snake stays down before it respawns (about 2.5s at 9 tps).
RESPAWN_TICKS = 22
# Keep at least this many food pellets on the board, plus one per extra player.
BASE_FOOD = 3
# Never let the board flood with food (e.g. after several deaths at once).
MAX_FOOD = 60
# When a snake dies its body scatters into pellets — capped so a long snake
# does not carpet the board.
MAX_DEATH_DROP = 6

# Directions as (dx, dy); x is the column, y is the row (both grow toward the
# bottom-right of the board).
DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

# Distinct, high-contrast snake colours handed out round-robin as players join.
PLAYER_COLORS = [
    "#2ecc71",  # emerald
    "#e74c3c",  # red
    "#3498db",  # blue
    "#f1c40f",  # yellow
    "#9b59b6",  # violet
    "#1abc9c",  # turquoise
    "#e67e22",  # orange
    "#ff6ec7",  # pink
    "#00e5ff",  # cyan
    "#a3e635",  # lime
]


class Player:
    """One snake in the shared world."""

    __slots__ = (
        "id", "name", "color", "body", "direction", "pending_direction",
        "alive", "score", "best", "respawn_at", "grow",
    )

    def __init__(self, pid, name, color):
        self.id = pid
        self.name = name
        self.color = color
        # Head is body[0]; the deque trails toward the tail.
        self.body = deque()
        self.direction = DIRECTIONS["right"]
        self.pending_direction = self.direction
        self.alive = False
        self.score = 0
        self.best = 0
        self.respawn_at = 0
        self.grow = 0

    @property
    def head(self):
        return self.body[0]

    def set_direction(self, name):
        """Buffer a turn, rejecting unknown keys and 180-degree reversals."""
        vec = DIRECTIONS.get(name)
        if vec is None:
            return
        # Block reversing straight back onto the neck (only matters once the
        # snake is at least length 2).
        opposite = (-self.direction[0], -self.direction[1])
        if len(self.body) > 1 and vec == opposite:
            return
        self.pending_direction = vec


class MultiplayerSnake:
    """Authoritative, network-free multiplayer Snake engine.

    Call :meth:`add_player` / :meth:`remove_player` as clients come and go,
    feed inputs through the returned :class:`Player`, and advance the world one
    frame at a time with :meth:`tick`. :meth:`snapshot` returns a JSON-ready
    view of the whole board.
    """

    def __init__(self, width=BOARD_WIDTH, height=BOARD_HEIGHT, rng=None):
        self.width = width
        self.height = height
        self.rng = rng or random.Random()
        self.players = {}
        self.food = set()
        self.tick_count = 0
        self._next_id = 1

    # -- player lifecycle --------------------------------------------------

    def add_player(self, name=None, color=None):
        pid = self._next_id
        self._next_id += 1
        if not name:
            name = f"Player {pid}"
        if color is None:
            color = PLAYER_COLORS[(pid - 1) % len(PLAYER_COLORS)]
        player = Player(pid, name[:16], color)
        self.players[pid] = player
        self._spawn(player)
        self._replenish_food()
        return player

    def remove_player(self, pid):
        self.players.pop(pid, None)

    # -- board helpers -----------------------------------------------------

    def _occupied_cells(self):
        cells = set()
        for p in self.players.values():
            if p.alive:
                cells.update(p.body)
        return cells

    def _free_cell(self, avoid):
        """A random empty cell, or ``None`` if the board is essentially full."""
        # Try random darts first (cheap when the board is mostly empty), then
        # fall back to an exhaustive scan so a crowded board still resolves.
        for _ in range(64):
            cell = (self.rng.randrange(self.width), self.rng.randrange(self.height))
            if cell not in avoid:
                return cell
        free = [
            (x, y)
            for x in range(self.width)
            for y in range(self.height)
            if (x, y) not in avoid
        ]
        return self.rng.choice(free) if free else None

    def _spawn(self, player):
        """Place ``player`` as a fresh short snake on a clear stretch."""
        blocked = self._occupied_cells() | self.food
        for _ in range(64):
            hx = self.rng.randrange(2, max(3, self.width - 2))
            hy = self.rng.randrange(1, max(2, self.height - 1))
            # Lay the body out to the left of the head, moving right.
            cells = [(hx - i, hy) for i in range(START_LENGTH)]
            if all(x >= 0 and (x, y) not in blocked for (x, y) in cells):
                player.body = deque(cells)
                player.direction = DIRECTIONS["right"]
                player.pending_direction = player.direction
                player.alive = True
                player.grow = 0
                player.score = 0
                return
        # Board is extremely crowded; drop a length-1 snake on any free cell.
        cell = self._free_cell(blocked)
        if cell is not None:
            player.body = deque([cell])
            player.direction = DIRECTIONS["right"]
            player.pending_direction = player.direction
            player.alive = True
            player.grow = 0
            player.score = 0

    def _food_target(self):
        return min(MAX_FOOD, BASE_FOOD + max(0, len(self.players) - 1))

    def _replenish_food(self):
        target = self._food_target()
        if len(self.food) >= target:
            return
        avoid = self._occupied_cells() | self.food
        while len(self.food) < target:
            cell = self._free_cell(avoid)
            if cell is None:
                break
            self.food.add(cell)
            avoid.add(cell)

    def _scatter_food(self, body):
        """Turn a dead snake's body into a few pellets (capped)."""
        living = self._occupied_cells()
        drops = 0
        # Sprinkle every other segment so the pellets aren't a solid wall.
        for idx, cell in enumerate(body):
            if drops >= MAX_DEATH_DROP or len(self.food) >= MAX_FOOD:
                break
            if idx % 2 == 0 and cell not in living:
                self.food.add(cell)
                drops += 1

    # -- the simulation ----------------------------------------------------

    def tick(self):
        """Advance the whole world one frame.

        Movement is simultaneous: every living snake computes its next head,
        then collisions are resolved against the post-move board so head-to-head
        crashes and tail-chasing behave fairly.
        """
        self.tick_count += 1
        self._handle_respawns()

        alive = [p for p in self.players.values() if p.alive]

        new_heads = {}
        eats = {}
        for p in alive:
            p.direction = p.pending_direction
            hx, hy = p.head
            new_heads[p.id] = (hx + p.direction[0], hy + p.direction[1])
            eats[p.id] = new_heads[p.id] in self.food

        # Cells still occupied by a body *after* this move: every segment except
        # each snake's tail (which vacates), unless the snake grows this tick.
        bodies_after = set()
        for p in alive:
            segs = list(p.body)
            keep = segs if (eats[p.id] or p.grow > 0) else segs[:-1]
            bodies_after.update(keep)

        # Head-to-head: any target cell claimed by more than one head kills all
        # claimants.
        head_counts = {}
        for pid, cell in new_heads.items():
            head_counts[cell] = head_counts.get(cell, 0) + 1

        dead = set()
        for p in alive:
            hx, hy = new_heads[p.id]
            if not (0 <= hx < self.width and 0 <= hy < self.height):
                dead.add(p.id)
            elif (hx, hy) in bodies_after:
                dead.add(p.id)
            elif head_counts[(hx, hy)] > 1:
                dead.add(p.id)

        # Apply results.
        for p in alive:
            if p.id in dead:
                p.alive = False
                p.respawn_at = self.tick_count + RESPAWN_TICKS
                self._scatter_food(list(p.body))
                p.body = deque()
                continue
            head = new_heads[p.id]
            p.body.appendleft(head)
            if eats[p.id]:
                self.food.discard(head)
                p.score += 1
                p.best = max(p.best, p.score)
                # Small, satisfying growth per pellet.
                p.grow += 1
            if p.grow > 0:
                p.grow -= 1
            else:
                p.body.pop()

        self._replenish_food()

    def _handle_respawns(self):
        for p in self.players.values():
            if not p.alive and p.respawn_at and self.tick_count >= p.respawn_at:
                p.respawn_at = 0
                self._spawn(p)

    # -- serialization -----------------------------------------------------

    def snapshot(self):
        """A compact, JSON-ready view of the whole world for broadcasting."""
        snakes = []
        for p in sorted(self.players.values(), key=lambda q: q.id):
            snakes.append({
                "id": p.id,
                "name": p.name,
                "color": p.color,
                "alive": p.alive,
                "score": p.score,
                "best": p.best,
                # Flatten to a plain list of [x, y] pairs for the client.
                "body": [[x, y] for (x, y) in p.body],
                "respawn_in": max(0, p.respawn_at - self.tick_count) if not p.alive else 0,
            })
        return {
            "type": "state",
            "tick": self.tick_count,
            "w": self.width,
            "h": self.height,
            "food": [[x, y] for (x, y) in sorted(self.food)],
            "snakes": snakes,
        }


# --- Minimal RFC 6455 WebSocket layer -------------------------------------

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def compute_accept(key):
    """Return the ``Sec-WebSocket-Accept`` value for a client key."""
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(payload, opcode=OP_TEXT):
    """Encode a single (unmasked, server->client) WebSocket frame."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    header = bytearray()
    header.append(0x80 | opcode)  # FIN + opcode
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + payload


async def read_frame(reader):
    """Read one WebSocket frame from ``reader``.

    Returns ``(opcode, bytes)`` or ``None`` on EOF/close. Handles the client
    masking that RFC 6455 mandates and reassembles simple fragmented messages.
    """
    data = bytearray()
    opcode_out = None
    while True:
        header = await reader.readexactly(2)
        b1, b2 = header[0], header[1]
        fin = b1 & 0x80
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await reader.readexactly(8))[0]
        mask = await reader.readexactly(4) if masked else b"\x00\x00\x00\x00"
        payload = bytearray(await reader.readexactly(length))
        if masked:
            for i in range(length):
                payload[i] ^= mask[i & 3]

        if opcode in (OP_CLOSE, OP_PING, OP_PONG):
            # Control frames are never fragmented; return immediately.
            return opcode, bytes(payload)
        if opcode != 0x0:  # a new data frame; remember its type
            opcode_out = opcode
        data += payload
        if fin:
            return opcode_out or OP_TEXT, bytes(data)


async def send_text(writer, text):
    writer.write(encode_frame(text, OP_TEXT))
    await writer.drain()


# --- HTTP + WebSocket server ----------------------------------------------

class GameServer:
    """Serves the client, upgrades WebSockets, and runs the game loop."""

    def __init__(self, host="0.0.0.0", port=8765):
        self.host = host
        self.port = port
        self.game = MultiplayerSnake()
        # Map player id -> StreamWriter for broadcasting.
        self.clients = {}

    async def start(self):
        server = await asyncio.start_server(self.handle_connection, self.host, self.port)
        loop = asyncio.get_event_loop()
        loop.create_task(self.game_loop())
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        print(f"Multiplayer Snake listening on {addrs}")
        print(f"  Open http://localhost:{self.port} in a browser (share your LAN IP for friends).")
        async with server:
            await server.serve_forever()

    async def game_loop(self):
        """Advance the world and broadcast state at a fixed rate."""
        interval = 1.0 / TICKS_PER_SECOND
        while True:
            await asyncio.sleep(interval)
            if not self.clients:
                continue
            self.game.tick()
            await self.broadcast(self.game.snapshot())

    async def broadcast(self, message):
        payload = json.dumps(message)
        frame = encode_frame(payload, OP_TEXT)
        dead = []
        for pid, writer in list(self.clients.items()):
            try:
                writer.write(frame)
                await writer.drain()
            except (ConnectionError, RuntimeError):
                dead.append(pid)
        for pid in dead:
            self._drop_client(pid)

    def _drop_client(self, pid):
        self.clients.pop(pid, None)
        self.game.remove_player(pid)

    async def handle_connection(self, reader, writer):
        try:
            request_line, headers = await self._read_http_request(reader)
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return

        if request_line is None:
            writer.close()
            return

        method, path, _ = request_line
        upgrade = headers.get("upgrade", "").lower()

        if upgrade == "websocket":
            await self._handle_websocket(reader, writer, headers)
            return

        # Plain HTTP: serve the client or a health check.
        await self._serve_http(writer, method, path)

    async def _read_http_request(self, reader):
        """Read request line + headers (up to the blank line)."""
        raw = await reader.readuntil(b"\r\n\r\n")
        text = raw.decode("latin-1")
        lines = text.split("\r\n")
        if not lines or not lines[0]:
            return None, {}
        parts = lines[0].split(" ")
        if len(parts) < 3:
            return None, {}
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return (parts[0], parts[1], parts[2]), headers

    async def _serve_http(self, writer, method, path):
        if path in ("/health", "/healthz"):
            body = b"ok"
            content_type = "text/plain; charset=utf-8"
        elif path == "/" or path.startswith("/?") or path == "/index.html":
            body = CLIENT_HTML.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            body = b"Not Found"
            response = (
                "HTTP/1.1 404 Not Found\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin-1") + body
            writer.write(response)
            await writer.drain()
            writer.close()
            return

        header = (
            "HTTP/1.1 200 OK\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1")
        writer.write(header + body)
        await writer.drain()
        writer.close()

    async def _handle_websocket(self, reader, writer, headers):
        key = headers.get("sec-websocket-key")
        if not key:
            writer.close()
            return
        accept = compute_accept(key)
        handshake = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("latin-1")
        writer.write(handshake)
        await writer.drain()

        player = self.game.add_player()
        self.clients[player.id] = writer
        await send_text(writer, json.dumps({
            "type": "welcome",
            "id": player.id,
            "name": player.name,
            "color": player.color,
            "w": self.game.width,
            "h": self.game.height,
            "tps": TICKS_PER_SECOND,
        }))
        # Push one immediate state so the client can draw before the next tick.
        await send_text(writer, json.dumps(self.game.snapshot()))

        try:
            while True:
                frame = await read_frame(reader)
                if frame is None:
                    break
                opcode, data = frame
                if opcode == OP_CLOSE:
                    break
                if opcode == OP_PING:
                    writer.write(encode_frame(data, OP_PONG))
                    await writer.drain()
                    continue
                if opcode != OP_TEXT:
                    continue
                self._handle_client_message(player, data)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._drop_client(player.id)
            try:
                writer.close()
            except Exception:
                pass

    def _handle_client_message(self, player, data):
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "dir":
            player.set_direction(msg.get("dir"))
        elif kind == "setname":
            name = str(msg.get("name", "")).strip()
            if name:
                player.name = name[:16]


# --- Embedded browser client ----------------------------------------------

CLIENT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Multiplayer Snake</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: #0d1117; color: #e6edf3; display: flex; flex-direction: column;
    align-items: center; min-height: 100vh;
  }
  h1 { font-size: 1.3rem; margin: 14px 0 4px; letter-spacing: .5px; }
  .sub { color: #8b949e; font-size: .85rem; margin-bottom: 10px; }
  #wrap { display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap;
          justify-content: center; padding: 0 12px 24px; }
  canvas { background: #010409; border: 2px solid #30363d; border-radius: 8px;
           box-shadow: 0 0 40px rgba(0,0,0,.6); touch-action: none; max-width: 96vw; height: auto; }
  #panel { min-width: 220px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
          padding: 12px 14px; margin-bottom: 12px; }
  .card h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .1em;
             color: #8b949e; margin: 0 0 8px; }
  #scores li { display: flex; align-items: center; gap: 8px; list-style: none;
               padding: 4px 0; font-variant-numeric: tabular-nums; }
  #scores { margin: 0; padding: 0; }
  .swatch { width: 12px; height: 12px; border-radius: 3px; flex: 0 0 auto; }
  .me { font-weight: 700; }
  .pname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dead { opacity: .5; }
  #status { font-size: .8rem; color: #8b949e; }
  input, button { font: inherit; }
  #name { background: #0d1117; border: 1px solid #30363d; color: #e6edf3;
          border-radius: 6px; padding: 6px 8px; width: 130px; }
  button { background: #238636; color: #fff; border: 0; border-radius: 6px;
           padding: 6px 12px; cursor: pointer; }
  button:hover { background: #2ea043; }
  .keys { font-size: .78rem; color: #8b949e; line-height: 1.6; }
  kbd { background: #30363d; border-radius: 4px; padding: 1px 6px; font-size: .75rem; }
  #overlay { position: fixed; inset: 0; display: none; align-items: center;
             justify-content: center; background: rgba(1,4,9,.55); font-size: 1.4rem;
             font-weight: 700; pointer-events: none; text-shadow: 0 2px 8px #000; }
</style>
</head>
<body>
  <h1>🐍 Multiplayer Snake</h1>
  <div class="sub">Realtime over WebSocket — steer with arrow keys or WASD</div>
  <div id="wrap">
    <canvas id="board" width="640" height="480"></canvas>
    <div id="panel">
      <div class="card">
        <h2>You</h2>
        <div style="display:flex; gap:8px; align-items:center;">
          <input id="name" maxlength="16" placeholder="Your name">
          <button id="rename">Set</button>
        </div>
      </div>
      <div class="card">
        <h2>Scoreboard</h2>
        <ul id="scores"></ul>
      </div>
      <div class="card">
        <h2>Status</h2>
        <div id="status">Connecting…</div>
      </div>
      <div class="card keys">
        <div><kbd>↑</kbd><kbd>↓</kbd><kbd>←</kbd><kbd>→</kbd> or <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> to steer</div>
        <div>Eat pellets to grow · crashing respawns you</div>
      </div>
    </div>
  </div>
  <div id="overlay"></div>
<script>
(function () {
  const canvas = document.getElementById('board');
  const ctx = canvas.getContext('2d');
  const scoresEl = document.getElementById('scores');
  const statusEl = document.getElementById('status');
  const overlay = document.getElementById('overlay');
  const nameInput = document.getElementById('name');
  const renameBtn = document.getElementById('rename');

  let ws = null;
  let myId = null;
  let cell = 16;          // pixels per board cell (recomputed from board size)
  let world = { w: 40, h: 30, snakes: [], food: [] };

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(proto + '://' + location.host + '/ws');
    ws.onopen = () => { statusEl.textContent = 'Connected — go!'; };
    ws.onclose = () => {
      statusEl.textContent = 'Disconnected — reconnecting…';
      setTimeout(connect, 1000);
    };
    ws.onerror = () => { statusEl.textContent = 'Connection error'; };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'welcome') {
        myId = msg.id;
        if (!nameInput.value) nameInput.placeholder = msg.name;
        resize(msg.w, msg.h);
      } else if (msg.type === 'state') {
        world = msg;
        resize(msg.w, msg.h);
        draw();
        updateScores();
      }
    };
  }

  function resize(w, h) {
    world.w = w; world.h = h;
    // Fit the board into a sensible pixel size while keeping square cells.
    const maxPx = Math.min(720, window.innerWidth - 40);
    cell = Math.max(8, Math.floor(maxPx / w));
    canvas.width = w * cell;
    canvas.height = h * cell;
  }

  function draw() {
    ctx.fillStyle = '#010409';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // faint grid
    ctx.strokeStyle = 'rgba(255,255,255,0.03)';
    ctx.lineWidth = 1;
    for (let x = 0; x <= world.w; x++) {
      ctx.beginPath(); ctx.moveTo(x * cell, 0); ctx.lineTo(x * cell, canvas.height); ctx.stroke();
    }
    for (let y = 0; y <= world.h; y++) {
      ctx.beginPath(); ctx.moveTo(0, y * cell); ctx.lineTo(canvas.width, y * cell); ctx.stroke();
    }

    // food
    for (const [fx, fy] of world.food) {
      ctx.fillStyle = '#ff5252';
      const cx = fx * cell + cell / 2, cy = fy * cell + cell / 2;
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(2, cell * 0.32), 0, Math.PI * 2);
      ctx.fill();
    }

    // snakes
    for (const s of world.snakes) {
      if (!s.body || !s.body.length) continue;
      const isMe = s.id === myId;
      for (let i = 0; i < s.body.length; i++) {
        const [x, y] = s.body[i];
        ctx.fillStyle = s.color;
        ctx.globalAlpha = s.alive ? (i === 0 ? 1 : 0.85) : 0.35;
        const pad = i === 0 ? 1 : 2;
        roundRect(x * cell + pad, y * cell + pad, cell - pad * 2, cell - pad * 2, 3);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      // outline my own head so I can find myself in a crowd
      if (isMe && s.alive) {
        const [hx, hy] = s.body[0];
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        roundRect(hx * cell + 1, hy * cell + 1, cell - 2, cell - 2, 3);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }

  function roundRect(x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function updateScores() {
    const sorted = [...world.snakes].sort((a, b) => b.score - a.score);
    scoresEl.innerHTML = '';
    let me = null;
    for (const s of sorted) {
      if (s.id === myId) me = s;
      const li = document.createElement('li');
      if (!s.alive) li.className = 'dead';
      const sw = document.createElement('span');
      sw.className = 'swatch'; sw.style.background = s.color;
      const nm = document.createElement('span');
      nm.className = 'pname' + (s.id === myId ? ' me' : '');
      nm.textContent = s.name + (s.id === myId ? ' (you)' : '');
      const sc = document.createElement('span');
      sc.textContent = s.score;
      li.append(sw, nm, sc);
      scoresEl.appendChild(li);
    }
    if (me && !me.alive) {
      overlay.style.display = 'flex';
      overlay.textContent = me.respawn_in > 0
        ? 'Crashed! Respawning…'
        : 'Respawning…';
    } else {
      overlay.style.display = 'none';
    }
  }

  const KEYS = {
    ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
    w: 'up', s: 'down', a: 'left', d: 'right',
    W: 'up', S: 'down', A: 'left', D: 'right',
  };
  function sendDir(dir) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'dir', dir }));
    }
  }
  window.addEventListener('keydown', (e) => {
    if (document.activeElement === nameInput) return;
    const dir = KEYS[e.key];
    if (dir) { e.preventDefault(); sendDir(dir); }
  });

  function submitName() {
    const name = nameInput.value.trim();
    if (name && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'setname', name }));
    }
    nameInput.blur();
  }
  renameBtn.addEventListener('click', submitName);
  nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitName(); });

  // Touch / swipe controls for phones.
  let touchStart = null;
  canvas.addEventListener('touchstart', (e) => {
    touchStart = e.touches[0]; e.preventDefault();
  }, { passive: false });
  canvas.addEventListener('touchend', (e) => {
    if (!touchStart) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touchStart.clientX;
    const dy = t.clientY - touchStart.clientY;
    if (Math.abs(dx) > Math.abs(dy)) sendDir(dx > 0 ? 'right' : 'left');
    else sendDir(dy > 0 ? 'down' : 'up');
    touchStart = null; e.preventDefault();
  }, { passive: false });

  connect();
})();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Realtime multiplayer Snake over WebSocket.")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"),
                        help="Interface to bind (default 0.0.0.0 = all).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")),
                        help="TCP port to listen on (default 8765).")
    args = parser.parse_args()

    server = GameServer(host=args.host, port=args.port)
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
