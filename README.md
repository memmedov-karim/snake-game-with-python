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

## Controls

| Key | Action |
| --- | --- |
| Arrow keys / `W` `A` `S` `D` | Steer the snake |
| `P` | Pause / resume |
| `R` | Restart (on the game-over screen) |
| `Q` | Quit |

## Rules

- Eat the food (`@`) to grow and score a point.
- The snake speeds up slightly with every point.
- Running into a wall or into your own body ends the round.
- Fill the whole board to win.
