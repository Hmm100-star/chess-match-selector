"""Utilities for applying match results back to Student_Information.csv."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd


NUMERIC_COLUMNS = [
    "Total Wins",
    "Total Losses",
    "Total Ties",
    "# Times Played White",
    "# Times Played Black",
    "Correct Homework",
    "Incorrect Homework",
]


BYE_COUNTS_AS_WIN = True  # Award listed players a win when they receive a bye row with no opponent.

REQUIRED_MATCH_COLUMNS = [
    "White Player",
    "White Player Strength",
    "Black Player",
    "Black Player Strength",
    "Who Won",
    "White Homework Correct",
    "White Homework Incorrect",
    "Black Homework Correct",
    "Black Homework Incorrect",
]


@dataclass
class ResultDelta:
    wins: int
    losses: int
    ties: int


def _normalise_name(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _parse_who_won(raw: Optional[str]) -> Optional[str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None

    value = str(raw).strip().lower()
    if not value:
        return None

    if value not in {"w", "b", "t"}:
        raise ValueError("Who Won must be W (white), B (black), or T (tie). Got: %r" % raw)

    mapping = {"w": "white", "b": "black", "t": "tie"}
    return mapping[value]


def _parse_homework(value: str) -> int:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0
    text = str(value).strip()
    if not text:
        return 0
    try:
        parsed = int(float(text))
    except ValueError as exc:  # pragma: no cover - defensive branch
        raise ValueError(f"Homework counts must be numeric, got {value!r}.") from exc
    if parsed < 0:
        raise ValueError("Homework counts cannot be negative.")
    return parsed


def _ensure_columns(df: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [col for col in columns if col not in df.columns]
    if missing:
        raise ValueError(f"Match CSV is missing required columns: {', '.join(missing)}")


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
    return df


def update_student_information(
    students_csv: Path, matches_csv: Path, output_csv: Path
) -> Path:
    """Update the Student_Information sheet using a completed next_matches.csv.

    The match sheet must include the "Who Won" column with values W/B/T (or blank
    on bye rows) plus per-colour homework correct/incorrect counts.
    """

    students_df = pd.read_csv(students_csv)
    students_df.columns = students_df.columns.str.strip()
    students_df = _coerce_numeric_columns(students_df)

    matches_df = pd.read_csv(matches_csv)
    matches_df.columns = matches_df.columns.str.strip()
    _ensure_columns(matches_df, REQUIRED_MATCH_COLUMNS)

    name_to_index: Dict[str, int] = {}
    for idx, name in enumerate(students_df["Student Name"]):
        normalised = _normalise_name(name)
        if not normalised:
            continue
        if normalised in name_to_index:
            raise ValueError(f"Duplicate student name detected: {name}")
        name_to_index[normalised] = idx

    def apply_player(
        name: str,
        color_field: str,
        result_delta: Optional[ResultDelta],
        correct_raw: str,
        incorrect_raw: str,
    ) -> None:
        player_name = _normalise_name(name)
        if not player_name:
            if result_delta and (result_delta.wins or result_delta.losses or result_delta.ties):
                raise ValueError("Cannot record results without a player name.")
            if any(_normalise_name(v) for v in [correct_raw, incorrect_raw]):
                raise ValueError("Cannot record homework without a player name.")
            return

        if player_name not in name_to_index:
            raise ValueError(f"Player '{player_name}' not found in Student_Information.csv")

        correct_delta = _parse_homework(correct_raw)
        incorrect_delta = _parse_homework(incorrect_raw)
        result_delta = result_delta or ResultDelta(0, 0, 0)

        student_index = name_to_index[player_name]
        students_df.at[student_index, color_field] += 1
        students_df.at[student_index, "Total Wins"] += result_delta.wins
        students_df.at[student_index, "Total Losses"] += result_delta.losses
        students_df.at[student_index, "Total Ties"] += result_delta.ties
        students_df.at[student_index, "Correct Homework"] += correct_delta
        students_df.at[student_index, "Incorrect Homework"] += incorrect_delta

    for _, row in matches_df.iterrows():
        white_name = _normalise_name(row.get("White Player"))
        black_name = _normalise_name(row.get("Black Player"))
        winner = _parse_who_won(row.get("Who Won"))

        if winner == "white" and not white_name:
            raise ValueError("Cannot record a White win without a White Player name.")
        if winner == "black" and not black_name:
            raise ValueError("Cannot record a Black win without a Black Player name.")
        if winner == "tie" and (not white_name or not black_name):
            raise ValueError("Cannot record a tie without both player names.")
        if winner is None and white_name and black_name:
            raise ValueError("Who Won must be W, B, or T when both players are listed.")
        if not white_name and not black_name:
            if winner:
                raise ValueError("Cannot record a result when both player names are blank.")
            if any(
                _normalise_name(row.get(key))
                for key in [
                    "White Homework Correct",
                    "White Homework Incorrect",
                    "Black Homework Correct",
                    "Black Homework Incorrect",
                ]
            ):
                raise ValueError("Cannot record homework without player names.")
            continue

        white_result: Optional[ResultDelta]
        black_result: Optional[ResultDelta]
        if winner == "white":
            white_result = ResultDelta(1, 0, 0)
            black_result = ResultDelta(0, 1, 0)
        elif winner == "black":
            white_result = ResultDelta(0, 1, 0)
            black_result = ResultDelta(1, 0, 0)
        elif winner == "tie":
            white_result = ResultDelta(0, 0, 1)
            black_result = ResultDelta(0, 0, 1)
        else:
            white_result = None
            black_result = None
            if white_name and not black_name:
                white_result = ResultDelta(1, 0, 0) if BYE_COUNTS_AS_WIN else ResultDelta(0, 0, 0)
            elif black_name and not white_name:
                black_result = ResultDelta(1, 0, 0) if BYE_COUNTS_AS_WIN else ResultDelta(0, 0, 0)

        if not white_name:
            white_result = None
        if not black_name:
            black_result = None

        apply_player(
            white_name,
            "# Times Played White",
            white_result,
            row.get("White Homework Correct"),
            row.get("White Homework Incorrect"),
        )
        apply_player(
            black_name,
            "# Times Played Black",
            black_result,
            row.get("Black Homework Correct"),
            row.get("Black Homework Incorrect"),
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    students_df.to_csv(output_csv, index=False)
    return output_csv


__all__ = ["update_student_information"]
