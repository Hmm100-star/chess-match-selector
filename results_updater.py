"""Utilities for applying match results back to Student_Information.csv."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple

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
    "Notes",
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


def _normalise_note(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _parse_who_won(raw: str) -> Tuple[ResultDelta, ResultDelta]:
    """Return per-colour deltas for a Who Won cell.

    Accepts W/B/T synonyms. Blank or "Bye" values produce zero deltas to leave
    win/loss/tie counts unchanged while still allowing colour tracking.
    """

    value = "" if raw is None else str(raw).strip().lower()
    if not value:
        return ResultDelta(0, 0, 0), ResultDelta(0, 0, 0)

    if value in {"white", "w", "1-0", "white win", "white player"}:
        return ResultDelta(1, 0, 0), ResultDelta(0, 1, 0)
    if value in {"black", "b", "0-1", "black win", "black player"}:
        return ResultDelta(0, 1, 0), ResultDelta(1, 0, 0)
    if value in {"tie", "draw", "t", "d", "0.5", "1/2", "1/2-1/2"}:
        return ResultDelta(0, 0, 1), ResultDelta(0, 0, 1)
    if value in {"bye"}:
        return ResultDelta(0, 0, 0), ResultDelta(0, 0, 0)

    raise ValueError("Who Won must be White, Black, Tie/Draw, Bye, or blank.")


def _has_result(delta: ResultDelta) -> bool:
    return any([delta.wins, delta.losses, delta.ties])


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


def _append_note(existing: str, incoming: str) -> str:
    if not incoming:
        return existing
    if not existing:
        return incoming
    return f"{existing}, {incoming}"


def update_student_information(
    students_csv: Path, matches_csv: Path, output_csv: Path
) -> Path:
    """Update the Student_Information sheet using a completed next_matches.csv.

    The match sheet must include the "Who Won" column with values W/B/T (or
    blank/Bye) plus per-colour homework correct/incorrect counts. Notes are
    appended to the rightmost "Notes" column in the student sheet.
    """

    students_df = pd.read_csv(students_csv)
    students_df.columns = students_df.columns.str.strip()

    if "Notes" not in students_df.columns:
        students_df["Notes"] = ""
    else:
        students_df["Notes"] = students_df["Notes"].fillna("").astype(str)
    # Ensure Notes is the rightmost column
    other_columns = [col for col in students_df.columns if col != "Notes"]
    students_df = students_df[other_columns + ["Notes"]]

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
        result_delta: ResultDelta,
        correct_raw: str,
        incorrect_raw: str,
        note_text: str,
    ) -> None:
        player_name = _normalise_name(name)
        correct_delta = _parse_homework(correct_raw)
        incorrect_delta = _parse_homework(incorrect_raw)
        has_note = bool(_normalise_note(note_text))

        if not player_name:
            if _has_result(result_delta) or correct_delta or incorrect_delta or has_note:
                raise ValueError("Cannot record results, homework, or notes without a player name.")
            return

        if player_name not in name_to_index:
            raise ValueError(f"Player '{player_name}' not found in Student_Information.csv")

        student_index = name_to_index[player_name]
        students_df.at[student_index, color_field] += 1
        students_df.at[student_index, "Total Wins"] += result_delta.wins
        students_df.at[student_index, "Total Losses"] += result_delta.losses
        students_df.at[student_index, "Total Ties"] += result_delta.ties
        students_df.at[student_index, "Correct Homework"] += correct_delta
        students_df.at[student_index, "Incorrect Homework"] += incorrect_delta

        if has_note:
            existing = students_df.at[student_index, "Notes"]
            students_df.at[student_index, "Notes"] = _append_note(existing, _normalise_note(note_text))

    for _, row in matches_df.iterrows():
        white_delta, black_delta = _parse_who_won(row.get("Who Won"))
        note_text = row.get("Notes", "")

        apply_player(
            row.get("White Player"),
            "# Times Played White",
            white_delta,
            row.get("White Homework Correct"),
            row.get("White Homework Incorrect"),
            note_text,
        )
        apply_player(
            row.get("Black Player"),
            "# Times Played Black",
            black_delta,
            row.get("Black Homework Correct"),
            row.get("Black Homework Incorrect"),
            note_text,
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    students_df.to_csv(output_csv, index=False)
    return output_csv


__all__ = ["update_student_information"]
