#!/usr/bin/env python3
"""A playable Snake game for the terminal.

Single file, standard library only (uses curses). Steer the snake with the
arrow keys or WASD, eat the food (``@``) to grow and score, and avoid running
into the walls or your own body.

Controls:
    Arrow keys / W A S D : change direction
    P                    : pause / resume
    Q                    : quit
    R                    : restart (from the game-over screen)

Run it with::

    python3 snake.py
"""

import curses
import random
from collections import deque

# How many milliseconds the game waits for input each frame. Lower is faster.
BASE_TICK_MS = 120
MIN_TICK_MS = 55
# The tick shrinks by this much for every food eaten, capped at MIN_TICK_MS.
SPEEDUP_MS_PER_FOOD = 4

# Direction vectors expressed as (delta_row, delta_col).
UP = (-1, 0)
DOWN = (1, 0)
LEFT = (0, -1)
RIGHT = (0, 1)

# Map every accepted key to a direction.
KEY_DIRECTIONS = {
    curses.KEY_UP: UP,
    curses.KEY_DOWN: DOWN,
    curses.KEY_LEFT: LEFT,
    curses.KEY_RIGHT: RIGHT,
    ord("w"): UP,
    ord("W"): UP,
    ord("s"): DOWN,
    ord("S"): DOWN,
    ord("a"): LEFT,
    ord("A"): LEFT,
    ord("d"): RIGHT,
    ord("D"): RIGHT,
}

# Playfield borders. The board occupies the interior of these bounds.
BORDER_ROWS = 2  # top border + bottom border
BORDER_COLS = 2  # left border + right border
# Leave a row at the top for the score/status header.
HEADER_ROWS = 1


class SnakeGame:
    """Holds the mutable state for a single round of Snake."""

    def __init__(self, height, width):
        # ``height``/``width`` are the interior play dimensions (rows, cols).
        self.height = height
        self.width = width
        self.reset()

    def reset(self):
        """Start a fresh round centred in the playfield."""
        start_row = self.height // 2
        start_col = self.width // 2
        # Head is the left end of the deque; the snake starts length 3 moving
        # right, so the body trails to the left of the head.
        self.snake = deque(
            [
                (start_row, start_col),
                (start_row, start_col - 1),
                (start_row, start_col - 2),
            ]
        )
        self.occupied = set(self.snake)
        self.direction = RIGHT
        # ``pending_direction`` buffers the next turn so a fast player cannot
        # reverse into themselves within a single tick.
        self.pending_direction = RIGHT
        self.score = 0
        self.paused = False
        self.game_over = False
        self.food = None
        self._place_food()

    def _place_food(self):
        """Drop food on a random empty cell, or win if the board is full."""
        free_cells = self.height * self.width - len(self.snake)
        if free_cells <= 0:
            # The snake fills the entire board: a win. Freeze the game.
            self.food = None
            self.game_over = True
            return
        while True:
            cell = (
                random.randint(0, self.height - 1),
                random.randint(0, self.width - 1),
            )
            if cell not in self.occupied:
                self.food = cell
                return

    def set_direction(self, new_direction):
        """Buffer a turn, ignoring 180-degree reversals."""
        opposite = (-self.direction[0], -self.direction[1])
        if new_direction != opposite:
            self.pending_direction = new_direction

    def toggle_pause(self):
        if not self.game_over:
            self.paused = not self.paused

    def step(self):
        """Advance the snake one cell. Returns False if the round ended."""
        if self.paused or self.game_over:
            return not self.game_over

        self.direction = self.pending_direction
        head_row, head_col = self.snake[0]
        new_head = (head_row + self.direction[0], head_col + self.direction[1])
        row, col = new_head

        # Wall collision.
        if not (0 <= row < self.height and 0 <= col < self.width):
            self.game_over = True
            return False

        # Self collision. The current tail cell is about to move away, so it is
        # a legal target unless the snake is about to grow.
        eating = new_head == self.food
        tail = self.snake[-1]
        if new_head in self.occupied and not (new_head == tail and not eating):
            self.game_over = True
            return False

        # Advance the head.
        self.snake.appendleft(new_head)
        self.occupied.add(new_head)

        if eating:
            self.score += 1
            self._place_food()
        else:
            # Move the tail forward by dropping the last segment.
            self.occupied.discard(self.snake.pop())

        return not self.game_over

    @property
    def tick_ms(self):
        """Current frame delay: speeds up as the score climbs."""
        return max(MIN_TICK_MS, BASE_TICK_MS - self.score * SPEEDUP_MS_PER_FOOD)


def _draw(stdscr, game):
    """Render the current game state to the screen."""
    stdscr.erase()

    # Header line with score and controls.
    status = "PAUSED" if game.paused else ("GAME OVER" if game.game_over else "")
    header = f" Score: {game.score}   {status}"
    controls = "Arrows/WASD move  P pause  Q quit "
    stdscr.addstr(0, 0, header[: game.width + BORDER_COLS])
    # Right-align the controls hint if there is room for it.
    total_width = game.width + BORDER_COLS
    if total_width - len(controls) - len(header) > 1:
        stdscr.addstr(0, total_width - len(controls), controls)

    # Border box drawn just below the header.
    top = HEADER_ROWS
    box_height = game.height + BORDER_ROWS
    box_width = game.width + BORDER_COLS
    stdscr.attron(curses.color_pair(1))
    for c in range(box_width):
        stdscr.addch(top, c, curses.ACS_HLINE if 0 < c < box_width - 1 else curses.ACS_CKBOARD)
        stdscr.addch(top + box_height - 1, c, curses.ACS_HLINE if 0 < c < box_width - 1 else curses.ACS_CKBOARD)
    for r in range(1, box_height - 1):
        stdscr.addch(top + r, 0, curses.ACS_VLINE)
        stdscr.addch(top + r, box_width - 1, curses.ACS_VLINE)
    stdscr.attroff(curses.color_pair(1))

    # Interior origin (where play cell (0,0) is drawn).
    origin_row = top + 1
    origin_col = 1

    # Food.
    if game.food is not None:
        fr, fc = game.food
        stdscr.attron(curses.color_pair(2))
        stdscr.addch(origin_row + fr, origin_col + fc, ord("@"))
        stdscr.attroff(curses.color_pair(2))

    # Snake: head bright, body dimmer.
    for index, (r, c) in enumerate(game.snake):
        char = ord("O") if index == 0 else ord("o")
        stdscr.attron(curses.color_pair(3))
        stdscr.addch(origin_row + r, origin_col + c, char)
        stdscr.attroff(curses.color_pair(3))

    # Overlays.
    if game.game_over:
        won = game.food is None and game.score > 0
        msg = "YOU WIN!  " if won else "GAME OVER  "
        msg += f"final score {game.score}"
        prompt = "Press R to restart or Q to quit"
        _center_text(stdscr, top + box_height // 2, box_width, msg)
        _center_text(stdscr, top + box_height // 2 + 1, box_width, prompt)

    stdscr.noutrefresh()
    curses.doupdate()


def _center_text(stdscr, row, width, text):
    col = max(0, (width - len(text)) // 2)
    try:
        stdscr.addstr(row, col, text, curses.A_BOLD)
    except curses.error:
        # Writing to the last cell can raise; ignore rather than crash.
        pass


def _run(stdscr):
    """curses entry point; owns the main loop."""
    curses.curs_set(0)  # hide the cursor
    stdscr.keypad(True)  # translate arrow keys into curses.KEY_* codes

    # Colours are optional; degrade gracefully to monochrome.
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLUE, -1)  # border
        curses.init_pair(2, curses.COLOR_RED, -1)  # food
        curses.init_pair(3, curses.COLOR_GREEN, -1)  # snake

    screen_h, screen_w = stdscr.getmaxyx()
    # Reserve space for the header and the border box.
    play_h = screen_h - HEADER_ROWS - BORDER_ROWS
    play_w = screen_w - BORDER_COLS

    min_h, min_w = 5, 10
    if play_h < min_h or play_w < min_w:
        stdscr.erase()
        stdscr.addstr(0, 0, "Terminal too small. Resize and rerun.")
        stdscr.addstr(1, 0, "Press any key to exit.")
        stdscr.nodelay(False)
        stdscr.getch()
        return

    game = SnakeGame(play_h, play_w)

    while True:
        stdscr.timeout(game.tick_ms)
        _draw(stdscr, game)

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            break
        elif key in (ord("p"), ord("P")):
            game.toggle_pause()
            continue
        elif key in (ord("r"), ord("R")) and game.game_over:
            game.reset()
            continue
        elif key in KEY_DIRECTIONS:
            game.set_direction(KEY_DIRECTIONS[key])

        game.step()


def main():
    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
