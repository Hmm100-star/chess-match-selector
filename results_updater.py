"""Utilities for applying match results back to Student_Information.csv."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

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

def _parse_who_won(raw: str) -> str | None:
    """Return a canonical winner marker: w, b, t, or None for blank."""

    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None

    value = str(raw).strip().lower()
    if not value:
        return None

    if value in {"w", "white"}:
        return "w"
    if value in {"b", "black"}:
        return "b"
    if value in {"t", "tie", "d", "draw"}:
        return "t"
    if value == "bye":
        return "w"

    raise ValueError("Who Won must be W, B, T, or blank.")


def _result_delta_for_color(color: str, who_won: str | None) -> ResultDelta:
    if who_won is None:
        return ResultDelta(0, 0, 0)

    if who_won == "t":
        return ResultDelta(0, 0, 1)

    if who_won == "w":
        return ResultDelta(1, 0, 0) if color == "white" else ResultDelta(0, 1, 0)
    if who_won == "b":
        return ResultDelta(0, 1, 0) if color == "white" else ResultDelta(1, 0, 0)

    raise ValueError(f"Unexpected winner token {who_won!r}.")


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
    """Update the Student_Information sheet using a completed next_matches.csv."""

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

    def update_player(
        name: str,
        who_won: str | None,
        correct_raw: str,
        incorrect_raw: str,
        color_field: str,
        color_label: str,
    ) -> None:
        player_name = _normalise_name(name)
        if not player_name:
            if any(_normalise_name(v) for v in [correct_raw, incorrect_raw]):
                raise ValueError("Cannot record results without a player name.")
            return

        if player_name not in name_to_index:
            raise ValueError(f"Player '{player_name}' not found in Student_Information.csv")

        result_delta = _result_delta_for_color(color_label, who_won)
        correct_delta = _parse_homework(correct_raw)
        incorrect_delta = _parse_homework(incorrect_raw)

        student_index = name_to_index[player_name]
        students_df.at[student_index, color_field] += 1
        students_df.at[student_index, "Total Wins"] += result_delta.wins
        students_df.at[student_index, "Total Losses"] += result_delta.losses
        students_df.at[student_index, "Total Ties"] += result_delta.ties
        students_df.at[student_index, "Correct Homework"] += correct_delta
        students_df.at[student_index, "Incorrect Homework"] += incorrect_delta

    for _, row in matches_df.iterrows():
        who_won = _parse_who_won(row.get("Who Won"))
        update_player(
            row.get("White Player"),
            who_won,
            row.get("White Homework Correct"),
            row.get("White Homework Incorrect"),
            "# Times Played White",
            "white",
        )
        update_player(
            row.get("Black Player"),
            who_won,
            row.get("Black Homework Correct"),
            row.get("Black Homework Incorrect"),
            "# Times Played Black",
            "black",
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    students_df.to_csv(output_csv, index=False)
    return output_csv


__all__ = ["update_student_information"]
