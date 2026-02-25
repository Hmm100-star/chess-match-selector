from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from pairing_logic import normalize_weights, select_pairings
from models import HomeworkEntry, Match, Student


@dataclass
class RatingRow:
    student_id: int
    name: str
    rating: float
    color_diff: int


def build_rating_dataframe(
    students: Iterable[Student], win_weight: float, homework_weight: float
) -> Tuple[pd.DataFrame, List[int]]:
    rows: List[RatingRow] = []

    def safe_int(value: Optional[int]) -> int:
        if value is None:
            return 0
        return int(value)

    for student in students:
        total_wins = safe_int(student.total_wins)
        total_losses = safe_int(student.total_losses)
        total_ties = safe_int(student.total_ties)
        total_games = total_wins + total_losses + total_ties
        win_rate = total_wins / total_games if total_games else 0
        homework_correct = safe_int(student.homework_correct)
        homework_incorrect = safe_int(student.homework_incorrect)
        homework_score_sum = float(getattr(student, "homework_score_sum", 0.0) or 0.0)
        homework_score_count = safe_int(getattr(student, "homework_score_count", 0))
        total_homework = homework_correct + homework_incorrect
        if homework_score_count > 0:
            homework_score = homework_score_sum / homework_score_count
        else:
            homework_score = homework_correct / total_homework if total_homework else 0
        rating = round((win_weight * win_rate) + (homework_weight * homework_score), 3)
        color_diff = safe_int(student.times_white) - safe_int(student.times_black)
        rows.append(
            RatingRow(
                student_id=student.id,
                name=student.name,
                rating=rating,
                color_diff=color_diff,
            )
        )

    df = pd.DataFrame(
        [
            {
                "student_id": row.student_id,
                "Student Name": row.name,
                "rating": row.rating,
                "color_diff": row.color_diff,
            }
            for row in rows
        ]
    )
    df = df.sort_values(by=["rating", "color_diff"], ascending=[False, True]).reset_index(
        drop=True
    )
    id_order = df["student_id"].tolist()
    return df, id_order


def generate_matches_for_students(
    students: Iterable[Student],
    win_weight: float,
    homework_weight: float,
    recent_opponents: Optional[Dict[frozenset[int], int]] = None,
    bye_counts: Optional[Dict[int, int]] = None,
    rematch_window: int = 2,
    avoid_recent_rematches: bool = True,
    rotate_byes: bool = True,
) -> Tuple[List[Tuple[int, int]], List[int], pd.DataFrame, List[int]]:
    normalized_win, normalized_homework = normalize_weights(win_weight, homework_weight)
    df, id_order = build_rating_dataframe(students, normalized_win, normalized_homework)
    matches, unpaired = select_pairings(
        df,
        rng=_random(),
        student_ids=id_order,
        recent_opponents=recent_opponents,
        rematch_window=rematch_window,
        avoid_recent_rematches=avoid_recent_rematches,
        bye_counts=bye_counts,
        rotate_byes=rotate_byes,
    )
    return matches, unpaired, df, id_order


def _random():
    import random

    return random.Random()


def create_match_records(
    students: List[Student],
    matches: List[Tuple[int, int]],
    unpaired: List[int],
    df: pd.DataFrame,
    id_order: List[int],
) -> List[Match]:
    index_to_student_id = {idx: id_order[idx] for idx in range(len(id_order))}
    records: List[Match] = []

    for white_idx, black_idx in matches:
        white_id = index_to_student_id[white_idx]
        black_id = index_to_student_id[black_idx]
        records.append(
            Match(
                white_student_id=white_id,
                black_student_id=black_id,
                white_strength=f"{df.at[white_idx, 'rating']:.3f}",
                black_strength=f"{df.at[black_idx, 'rating']:.3f}",
                result=None,
                notes="",
                updated_at=datetime.utcnow(),
            )
        )

    for index in unpaired:
        student_id = index_to_student_id[index]
        records.append(
            Match(
                white_student_id=student_id,
                black_student_id=None,
                white_strength=f"{df.at[index, 'rating']:.3f}",
                black_strength=None,
                result="bye",
                notes="",
                updated_at=datetime.utcnow(),
            )
        )

    return records


def recalculate_totals(students: Iterable[Student], matches: Iterable[Match]) -> None:
    student_map = {student.id: student for student in students}
    for student in student_map.values():
        student.total_wins = 0
        student.total_losses = 0
        student.total_ties = 0
        student.times_white = 0
        student.times_black = 0
        student.homework_correct = 0
        student.homework_incorrect = 0
        student.homework_score_sum = 0.0
        student.homework_score_count = 0

    for match in matches:
        white_id = match.white_student_id
        black_id = match.black_student_id
        result = (match.result or "").lower()
        homework: Optional[HomeworkEntry] = match.homework_entry
        round_record = match.round

        if white_id and white_id in student_map:
            student_map[white_id].times_white += 1
        if black_id and black_id in student_map:
            student_map[black_id].times_black += 1

        if result == "white" and white_id and black_id:
            student_map[white_id].total_wins += 1
            student_map[black_id].total_losses += 1
        elif result == "black" and white_id and black_id:
            student_map[black_id].total_wins += 1
            student_map[white_id].total_losses += 1
        elif result == "tie" and white_id and black_id:
            student_map[white_id].total_ties += 1
            student_map[black_id].total_ties += 1

        def apply_homework(
            student_id: Optional[int],
            correct: int,
            incorrect: int,
            submitted: bool,
        ) -> None:
            if not student_id or student_id not in student_map:
                return

            student = student_map[student_id]
            student.homework_correct += int(correct)
            student.homework_incorrect += int(incorrect)

            policy = getattr(round_record, "homework_missing_policy", "zero") or "zero"
            penalty_wrong_pct = int(
                getattr(round_record, "homework_missing_penalty_wrong_pct", 100) or 100
            )
            penalty_wrong_pct = max(0, min(100, penalty_wrong_pct))
            total_questions = int(getattr(round_record, "homework_total_questions", 0) or 0)

            if submitted:
                if total_questions > 0:
                    denominator = total_questions
                else:
                    denominator = int(correct) + int(incorrect)
                if denominator > 0:
                    score = max(0.0, min(1.0, float(correct) / float(denominator)))
                    student.homework_score_sum += score
                    student.homework_score_count += 1
                return

            if policy == "exclude":
                return
            if policy == "penalty":
                score = 1 - (penalty_wrong_pct / 100.0)
                student.homework_score_sum += max(0.0, min(1.0, score))
                student.homework_score_count += 1
                return

            student.homework_score_sum += 0.0
            student.homework_score_count += 1

        if homework:
            apply_homework(
                white_id,
                homework.white_correct,
                homework.white_incorrect,
                bool(homework.white_submitted),
            )
            apply_homework(
                black_id,
                homework.black_correct,
                homework.black_incorrect,
                bool(homework.black_submitted),
            )
        else:
            apply_homework(white_id, 0, 0, False)
            apply_homework(black_id, 0, 0, False)
