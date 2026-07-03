# snake-game-with-python

A playable terminal Snake game written in a single Python file using only the
standard library (`curses`).

## Requirements

- Python 3.8+
- A terminal that supports `curses` (any Linux/macOS terminal; on Windows use
  WSL or install `windows-curses`).

## Play

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
