# snake-game-with-python

Two ways to play, both **pure Python standard library** (no `pip install`):

- **`snake.py`** — the classic single-player terminal game (`curses`).
- **`multiplayer.py`** — realtime **multiplayer** Snake served over WebSockets;
  everyone plays in the browser on one shared board. See
  [Multiplayer](#multiplayer-realtime-over-websocket) below.

## Requirements

- Python 3.8+
- Single-player: a terminal that supports `curses` (any Linux/macOS terminal; on
  Windows use WSL or install `windows-curses`).
- Multiplayer: just Python 3.8+ and a modern web browser — the WebSocket server
  is implemented on top of `asyncio` with no third-party dependencies.

## Play (single player)

```bash
python3 snake.py
```

Give the terminal a bit of room — at least ~12 rows by ~12 columns.

When the game starts you land on a **colour picker** — choose the snake theme
you want (each option shows a live preview), then press `Enter` to play.

## Controls

### Colour picker (start screen)

| Key | Action |
| --- | --- |
| Arrow keys / `W` `A` `S` `D` | Move the highlight between snake colours |
| `Enter` / `Space` | Start the game with the highlighted colour |
| `Q` | Quit |

### In game

| Key | Action |
| --- | --- |
| Arrow keys / `W` `A` `S` `D` | Steer the snake |
| `C` | Cycle the snake colour on the fly |
| `P` | Pause / resume |
| `R` | Restart (on the game-over screen) |
| `Q` | Quit |

## Rules

- Eat the food (`@`) to grow and score a point.
- The snake speeds up slightly with every point.
- Running into a wall or into your own body ends the round.
- Fill the whole board to win.

## Super visualization

The game ships with a set of terminal eye-candy effects (all standard-library
`curses`, no extra dependencies):

- **Selectable snake colours** — pick from five themes (Emerald, Fire, Ocean,
  Violet, Rainbow) on the start screen, and switch live in-game with `C`. Each
  theme is a flowing colour ramp that travels along the snake as it moves, led
  by a bright bold head. On 256-colour terminals the ramps are smooth
  gradients; elsewhere they fall back to a base-colour cycle.
- **Pulsing food** — the food breathes: it cycles colours and blinks between
  `@` and `*` so it is easy to spot.
- **Particle bursts** — eating food throws a ring of sparks (`* + . '`) that
  fly outward and fade over a few frames.
- **Textured background** — the empty board is drawn as a faint dotted grid.
- **Live HUD** — the header shows the current score, a persistent **Best**
  score that survives restarts, and flashes on each bite.
- **Animated end screen** — the win / game-over banner pulses and the prompt
  blinks.

Everything degrades gracefully to monochrome on terminals without colour
support, and drawing is size-safe on very small or exactly-filled terminals.

## Multiplayer (realtime over WebSocket)

`multiplayer.py` turns Snake into a shared, realtime arena you play in the
browser. One Python process both serves the client and runs an **authoritative
game loop**, broadcasting the whole board to every player each tick over
WebSockets.

### Run the server

```bash
python3 multiplayer.py                 # listens on http://0.0.0.0:8765
python3 multiplayer.py --port 9000     # pick a port
PORT=9000 python3 multiplayer.py       # env var works too
```

Then open **http://localhost:8765** in one or more browser tabs. To play with
friends on the same network, share your machine's LAN address (e.g.
`http://192.168.1.20:8765`).

### How it plays

- Every browser that connects gets its own snake with a distinct colour; your
  own head is outlined in white so you can find yourself in a crowd.
- Steer with the **arrow keys** or **WASD** (swipe on touch screens).
- Eat the red pellets to grow and score. Pellets are shared — race your rivals
  to them.
- Crashing into a **wall**, **another snake's body**, or a **head-to-head**
  collision kills you; your body scatters into fresh pellets and you respawn a
  couple of seconds later.
- A live **scoreboard** ranks everyone by score in realtime.
- Set a display name in the side panel any time.

### Under the hood (standard library only)

- The WebSocket protocol (RFC 6455 handshake + frame masking/codec) is
  implemented directly on top of `asyncio` — **no third-party packages**.
- `MultiplayerSnake` is a pure, network-free engine (movement, food,
  simultaneous-move collision resolution, respawns, scoring), so the game logic
  is fully unit-testable without any sockets.
- The browser client (canvas renderer + input + scoreboard) is embedded in the
  server file and served over plain HTTP, so there is no build step.

### Tests

```bash
python3 test_multiplayer.py
```

Covers the engine (movement, eating/growth, wall/body/head-to-head collisions,
respawn, snapshot serialization) and the network layer (RFC 6455 accept-key,
masked-frame decode, frame length framing) and finishes with a **live
end-to-end round-trip** against a real server instance.
