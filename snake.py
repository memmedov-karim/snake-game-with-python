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

# --- Super visualization tuning -------------------------------------------
# Characters used to fade a particle out as it ages (brightest -> gone).
PARTICLE_CHARS = ["*", "+", ".", "'"]
# How many sparks burst out of the food when it is eaten.
BURST_COUNT = 12
# Frames a spark lives before it vanishes.
PARTICLE_LIFE = len(PARTICLE_CHARS)
# The 8 directions a spark can fly, as (drow, dcol).
_SPARK_DIRS = [
    (-1, 0), (1, 0), (0, -1), (0, 1),
    (-1, -1), (-1, 1), (1, -1), (1, 1),
]
# Faint dotted background so the empty board reads as a textured grid.
BACKGROUND_STEP = 2  # draw a dot every N cells
BACKGROUND_CHAR = ord(".")

# --- Selectable snake colour themes ---------------------------------------
# Each theme gives the snake body a distinct look. ``ramp`` is a smooth xterm
# 256-colour gradient used on rich terminals (head -> tail); ``fallback`` is a
# short cycle of the 8 base curses colours for everything else. Both degrade to
# monochrome when the terminal has no colour at all.
SNAKE_THEMES = [
    {
        "name": "Emerald",
        "ramp": [22, 28, 34, 40, 46, 47, 48, 49, 50, 51],  # green -> cyan
        "fallback": ["GREEN", "CYAN", "BLUE", "MAGENTA"],
    },
    {
        "name": "Fire",
        "ramp": [52, 88, 124, 160, 196, 202, 208, 214, 220, 226],  # red -> yellow
        "fallback": ["RED", "YELLOW", "MAGENTA", "WHITE"],
    },
    {
        "name": "Ocean",
        "ramp": [17, 18, 19, 20, 21, 27, 33, 39, 45, 51],  # deep blue -> cyan
        "fallback": ["BLUE", "CYAN", "WHITE", "GREEN"],
    },
    {
        "name": "Violet",
        "ramp": [53, 54, 55, 56, 57, 93, 129, 165, 201, 207],  # purple -> pink
        "fallback": ["MAGENTA", "BLUE", "RED", "CYAN"],
    },
    {
        "name": "Rainbow",
        "ramp": [196, 208, 220, 46, 51, 21, 93, 201, 198, 160],  # full spectrum
        "fallback": ["RED", "YELLOW", "GREEN", "CYAN", "BLUE", "MAGENTA"],
    },
]


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
        self.best_score = getattr(self, "best_score", 0)
        self.paused = False
        self.game_over = False
        # Live spark particles emitted when food is eaten. Each entry is a
        # mutable [row, col, drow, dcol, life] list aged once per frame.
        self.particles = []
        # Frames since the last food was eaten; drives the "flash" reaction.
        self.eat_flash = 0
        self.food = None
        self._place_food()

    def _spawn_burst(self, row, col):
        """Emit a ring of sparks from ``(row, col)`` for the eat animation."""
        for drow, dcol in _SPARK_DIRS:
            self.particles.append([float(row), float(col), drow, dcol, PARTICLE_LIFE])
        # A few extra fast sparks add some liveliness to the burst.
        for i in range(BURST_COUNT - len(_SPARK_DIRS)):
            drow, dcol = _SPARK_DIRS[i % len(_SPARK_DIRS)]
            self.particles.append([float(row), float(col), drow * 2, dcol * 2, PARTICLE_LIFE - 1])

    def advance_effects(self):
        """Age particles and cool-down the eat flash. Runs every frame."""
        if self.eat_flash > 0:
            self.eat_flash -= 1
        if not self.particles:
            return
        alive = []
        for p in self.particles:
            p[4] -= 1
            if p[4] <= 0:
                continue
            p[0] += p[2] * 0.5
            p[1] += p[3] * 0.5
            alive.append(p)
        self.particles = alive

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
            self.best_score = max(self.best_score, self.score)
            self.eat_flash = 3
            self._spawn_burst(row, col)
            self._place_food()
        else:
            # Move the tail forward by dropping the last segment.
            self.occupied.discard(self.snake.pop())

        return not self.game_over

    @property
    def tick_ms(self):
        """Current frame delay: speeds up as the score climbs."""
        return max(MIN_TICK_MS, BASE_TICK_MS - self.score * SPEEDUP_MS_PER_FOOD)


class Palette:
    """Allocates curses colour pairs for the super-visualization renderer.

    Everything degrades gracefully: with no colour support the pair ids fall
    back to 0 (the terminal default) so the game still draws in monochrome.
    """

    def __init__(self):
        self.enabled = False
        self.border = 0
        # One gradient (head -> tail) per entry in SNAKE_THEMES; the selected
        # one is exposed via the ``snake`` property below. Defaults to a
        # monochrome ramp per theme so indexing is safe before ``setup``.
        self.snake_themes = [[0] for _ in SNAKE_THEMES]
        # Which theme is active. Steered by the start-screen selector and the
        # in-game "cycle colour" key.
        self.theme_index = 0
        # Colours the food pulses between, frame by frame.
        self.food = [0]
        # Bright spark colours for the eat burst.
        self.spark = [0]
        # Faint colour for the dotted background grid.
        self.background = 0

    @property
    def snake(self):
        """The colour ramp for the currently selected snake theme."""
        return self.snake_themes[self.theme_index]

    @property
    def theme_name(self):
        return SNAKE_THEMES[self.theme_index]["name"]

    def cycle_theme(self, step=1):
        """Move the selection to the next/previous snake theme."""
        self.theme_index = (self.theme_index + step) % len(SNAKE_THEMES)

    def setup(self):
        if not curses.has_colors():
            # Still expose one entry per theme so the selector works in mono.
            self.snake_themes = [[0] for _ in SNAKE_THEMES]
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        self.enabled = True

        pair = [1]  # next free pair id (mutable so the closure can bump it)

        def make(fg):
            curses.init_pair(pair[0], fg, bg)
            pid = curses.color_pair(pair[0])
            pair[0] += 1
            return pid

        self.border = make(curses.COLOR_BLUE)
        self.background = make(curses.COLOR_BLUE)

        # Build a body gradient for every theme. On richer terminals we use the
        # smooth 256-colour ramp; otherwise cycle the theme's base colours.
        rich = curses.COLORS >= 256 and curses.can_change_color()
        self.snake_themes = []
        for theme in SNAKE_THEMES:
            if rich:
                self.snake_themes.append([make(c) for c in theme["ramp"]])
            else:
                self.snake_themes.append(
                    [make(getattr(curses, "COLOR_" + name)) for name in theme["fallback"]]
                )

        self.food = [
            make(curses.COLOR_RED),
            make(curses.COLOR_YELLOW),
            make(curses.COLOR_MAGENTA),
        ]
        self.spark = [
            make(curses.COLOR_YELLOW),
            make(curses.COLOR_WHITE),
            make(curses.COLOR_CYAN),
        ]


def _draw(stdscr, game, palette, frame):
    """Render the current game state to the screen."""
    stdscr.erase()

    # Header line with score, best and controls.
    status = "PAUSED" if game.paused else ("GAME OVER" if game.game_over else "")
    header = f" Score: {game.score}  Best: {game.best_score}  [{palette.theme_name}]  {status}"
    controls = "Arrows/WASD move  C colour  P pause  Q quit "
    total_width = game.width + BORDER_COLS
    # Briefly bold the header the moment food is eaten, for a satisfying pop.
    header_attr = curses.A_BOLD if game.eat_flash > 0 else curses.A_NORMAL
    stdscr.addstr(0, 0, header[:total_width], header_attr)
    # Right-align the controls hint if there is room for it.
    if total_width - len(controls) - len(header) > 1:
        stdscr.addstr(0, total_width - len(controls), controls, curses.A_DIM)

    # Border box drawn just below the header.
    top = HEADER_ROWS
    box_height = game.height + BORDER_ROWS
    box_width = game.width + BORDER_COLS
    border_attr = palette.border | curses.A_BOLD
    for c in range(box_width):
        h = curses.ACS_HLINE if 0 < c < box_width - 1 else curses.ACS_CKBOARD
        _safe_addch(stdscr, top, c, h, border_attr)
        _safe_addch(stdscr, top + box_height - 1, c, h, border_attr)
    for r in range(1, box_height - 1):
        _safe_addch(stdscr, top + r, 0, curses.ACS_VLINE, border_attr)
        _safe_addch(stdscr, top + r, box_width - 1, curses.ACS_VLINE, border_attr)

    # Interior origin (where play cell (0,0) is drawn).
    origin_row = top + 1
    origin_col = 1

    # Faint dotted background grid so the empty board reads as a textured field.
    stdscr.attron(palette.background | curses.A_DIM)
    for r in range(0, game.height, BACKGROUND_STEP):
        for c in range(0, game.width, BACKGROUND_STEP):
            stdscr.addch(origin_row + r, origin_col + c, BACKGROUND_CHAR)
    stdscr.attroff(palette.background | curses.A_DIM)

    # Food: pulses through its colours and blinks between two glyphs so it
    # visibly "breathes" on the board.
    if game.food is not None:
        fr, fc = game.food
        food_color = palette.food[(frame // 2) % len(palette.food)]
        food_char = ord("@") if (frame // 3) % 2 == 0 else ord("*")
        stdscr.attron(food_color | curses.A_BOLD)
        stdscr.addch(origin_row + fr, origin_col + fc, food_char)
        stdscr.attroff(food_color | curses.A_BOLD)

    # Snake: a flowing rainbow gradient from a bright bold head down the body.
    for index, (r, c) in enumerate(game.snake):
        if index == 0:
            char = ord("O")
            attr = palette.snake[0] | curses.A_BOLD
        else:
            char = ord("o")
            # Shift the gradient over time so colour appears to travel the body.
            grad = (index + frame // 2) % len(palette.snake)
            attr = palette.snake[grad]
        stdscr.addch(origin_row + r, origin_col + c, char, attr)

    # Spark particles from the most recent food burst, fading as they age.
    for prow, pcol, _dr, _dc, life in game.particles:
        r = int(round(prow))
        c = int(round(pcol))
        if not (0 <= r < game.height and 0 <= c < game.width):
            continue
        char = PARTICLE_CHARS[min(len(PARTICLE_CHARS) - 1, PARTICLE_LIFE - life)]
        color = palette.spark[life % len(palette.spark)]
        try:
            stdscr.addch(origin_row + r, origin_col + c, ord(char), color | curses.A_BOLD)
        except curses.error:
            pass

    # Overlays.
    if game.game_over:
        won = game.food is None and game.score > 0
        msg = "YOU WIN!  " if won else "GAME OVER  "
        msg += f"final score {game.score}"
        prompt = "Press R to restart or Q to quit"
        # Pulse the banner colour so the end screen feels alive.
        end_colors = palette.spark if won else palette.food
        banner_attr = end_colors[(frame // 2) % len(end_colors)] | curses.A_BOLD
        _center_text(stdscr, top + box_height // 2, box_width, msg, banner_attr)
        # Blink the prompt roughly twice a second.
        prompt_attr = curses.A_BOLD if (frame // 3) % 2 == 0 else curses.A_DIM
        _center_text(stdscr, top + box_height // 2 + 1, box_width, prompt, prompt_attr)

    stdscr.noutrefresh()
    curses.doupdate()


def _safe_addch(stdscr, row, col, ch, attr=0):
    """addch that tolerates the bottom-right cell (which curses reports as ERR).

    Drawing to the very last cell of the screen scrolls the window and raises
    ``curses.error``; for decorative glyphs we simply skip that cell.
    """
    try:
        stdscr.addch(row, col, ch, attr)
    except curses.error:
        pass


def _center_text(stdscr, row, width, text, attr=curses.A_BOLD):
    col = max(0, (width - len(text)) // 2)
    try:
        stdscr.addstr(row, col, text, attr)
    except curses.error:
        # Writing to the last cell can raise; ignore rather than crash.
        pass


def _select_theme(stdscr, palette):
    """Start-screen menu to pick a snake colour. Returns False to quit.

    Up/Down (or W/S) move the highlight, Left/Right cycle too, Enter/Space
    starts the game and Q quits. Each option previews the actual snake colours.
    """
    stdscr.nodelay(False)
    stdscr.timeout(-1)
    frame = 0
    while True:
        stdscr.erase()
        screen_h, screen_w = stdscr.getmaxyx()
        limit = max(0, screen_w - 1)

        title = "S N A K E"
        subtitle = "Choose your snake colour"
        _center_text(stdscr, 1, screen_w, title[:limit], curses.A_BOLD)
        _center_text(stdscr, 2, screen_w, subtitle[:limit], curses.A_DIM)

        # One row per theme, each showing a little animated snake preview so the
        # player sees the real colours before committing.
        first_row = 4
        for i, theme in enumerate(SNAKE_THEMES):
            row = first_row + i
            if row >= screen_h - 2:
                break
            selected = i == palette.theme_index
            marker = "> " if selected else "  "
            name = theme["name"].ljust(9)
            label = f"{marker}{name} "
            attr = curses.A_BOLD if selected else curses.A_NORMAL
            col = max(0, (screen_w - 24) // 2)
            try:
                stdscr.addstr(row, col, label[:limit], attr)
            except curses.error:
                pass
            # Draw a short preview snake using this theme's ramp.
            ramp = palette.snake_themes[i]
            preview_col = col + len(label)
            for seg in range(8):
                ch = ord("O") if seg == 0 else ord("o")
                grad = (seg + frame // 2) % len(ramp)
                cattr = ramp[grad] | (curses.A_BOLD if selected else curses.A_NORMAL)
                _safe_addch(stdscr, row, preview_col + seg, ch, cattr)

        hint = "Up/Down select   Enter start   Q quit"
        if screen_h - 1 >= first_row + len(SNAKE_THEMES) + 1:
            _center_text(stdscr, screen_h - 1, screen_w, hint[:limit], curses.A_DIM)

        stdscr.noutrefresh()
        curses.doupdate()

        # Animate the previews while waiting for a keypress.
        stdscr.timeout(120)
        key = stdscr.getch()
        frame += 1
        if key in (ord("q"), ord("Q")):
            return False
        elif key in (curses.KEY_ENTER, ord("\n"), ord("\r"), ord(" ")):
            return True
        elif key in (curses.KEY_UP, ord("w"), ord("W"), curses.KEY_LEFT,
                     ord("a"), ord("A")):
            palette.cycle_theme(-1)
        elif key in (curses.KEY_DOWN, ord("s"), ord("S"), curses.KEY_RIGHT,
                     ord("d"), ord("D")):
            palette.cycle_theme(1)


def _run(stdscr):
    """curses entry point; owns the main loop."""
    curses.curs_set(0)  # hide the cursor
    stdscr.keypad(True)  # translate arrow keys into curses.KEY_* codes

    # Colours are optional; the Palette degrades gracefully to monochrome.
    palette = Palette()
    palette.setup()

    screen_h, screen_w = stdscr.getmaxyx()
    # Reserve space for the header and the border box.
    play_h = screen_h - HEADER_ROWS - BORDER_ROWS
    play_w = screen_w - BORDER_COLS

    min_h, min_w = 5, 10
    if play_h < min_h or play_w < min_w:
        stdscr.erase()
        # Truncate to the available width so even a tiny terminal won't crash.
        limit = max(0, screen_w - 1)
        for row, line in enumerate(("Terminal too small. Resize and rerun.",
                                    "Press any key to exit.")):
            if row < screen_h:
                try:
                    stdscr.addstr(row, 0, line[:limit])
                except curses.error:
                    pass
        stdscr.nodelay(False)
        stdscr.getch()
        return

    # Let the player pick a snake colour before the round begins. Bailing out
    # of the menu quits the whole game.
    if not _select_theme(stdscr, palette):
        return

    game = SnakeGame(play_h, play_w)
    frame = 0

    while True:
        stdscr.timeout(game.tick_ms)
        _draw(stdscr, game, palette, frame)
        frame += 1
        game.advance_effects()

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            break
        elif key in (ord("p"), ord("P")):
            game.toggle_pause()
            continue
        elif key in (ord("r"), ord("R")) and game.game_over:
            game.reset()
            continue
        elif key in (ord("c"), ord("C")):
            # Cycle the snake colour live without interrupting the round.
            palette.cycle_theme(1)
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
