#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan ordinary tile clicks for an observed 3x3 sliding board."""

import argparse
import heapq
import json
from pathlib import Path
import re


def read_board(log):
    rows = re.findall(r"Sliding: row ([0-9]+):([^\n]+)", log)
    if len(rows) < 3 or [int(row) for row, _ in rows[:3]] != [0, 1, 2]:
        raise ValueError("Expected the first three rows of an observed 3x3 board")
    board = tuple(int(number) for _, row in rows[:3] for number in re.findall(r"\d+", row))
    if len(board) != 9 or set(board) != {0, 2, 3, 4, 5, 6, 7, 8, 9}:
        raise ValueError("Expected a 3x3 board with top-left home gap and tiles 2..9")
    return board


def solve(board):
    goal = (0, 2, 3, 4, 5, 6, 7, 8, 9)

    def distance(state):
        return sum(abs(index // 3 - (tile - 1) // 3) + abs(index % 3 - (tile - 1) % 3)
                   for index, tile in enumerate(state) if tile)

    queue = [(distance(board), 0, board)]
    previous, best = {board: None}, {board: 0}
    while queue:
        _, steps, state = heapq.heappop(queue)
        if steps != best[state]:
            continue
        if state == goal:
            clicks = []
            while previous[state] is not None:
                old, clicked = previous[state]
                clicks.append(clicked)
                state = old
            return clicks[::-1]
        gap = state.index(0)
        for clicked in range(9):
            if abs(clicked // 3 - gap // 3) + abs(clicked % 3 - gap % 3) != 1:
                continue
            candidate = list(state)
            candidate[gap], candidate[clicked] = candidate[clicked], candidate[gap]
            candidate = tuple(candidate)
            if steps + 1 < best.get(candidate, 10 ** 9):
                best[candidate] = steps + 1
                previous[candidate] = (state, clicked)
                heapq.heappush(queue, (steps + 1 + distance(candidate), steps + 1, candidate))
    raise ValueError("Observed board has no legal solution")


def schedule(board, first_ms=3500, interval_ms=250):
    clicked = solve(board)
    side = 490 // 3
    x0, y0 = (800 - side * 3) // 2, (600 - side * 3) // 2
    return [{"time_ms": first_ms + n * interval_ms, "cell": cell,
             "x": x0 + (cell % 3) * side + side // 2,
             "y": y0 + (cell // 3) * side + side // 2} for n, cell in enumerate(clicked)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--first-ms", type=int, default=3500)
    args = parser.parse_args()
    board = read_board(args.log.read_text())
    moves = schedule(board, args.first_ms)
    print(json.dumps({"observed_board": board, "moves": moves,
                      "autoclick": ";".join(f"{m['time_ms']}:{m['x']},{m['y']}" for m in moves),
                      "scope": "A* plans legal adjacent-tile moves; the app must execute mouse events and persist solved state to validate completion"}, indent=2))


if __name__ == "__main__":
    main()
