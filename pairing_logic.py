import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


def normalize_weights(win_weight: float, homework_weight: float) -> Tuple[float, float]:
    """Normalise weights to sum to 1, ensuring they remain positive."""
    total = win_weight + homework_weight
    if total <= 0:
        raise ValueError("Win and homework weights must sum to a positive value.")
    return win_weight / total, homework_weight / total


def load_and_prepare_players(
    csv_path: Path, win_weight: float, homework_weight: float
) -> pd.DataFrame:
    """Load player data, normalise numeric fields, and compute derived metrics."""
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()

    numeric_columns = [
        "Total Wins",
        "Total Losses",
        "Total Ties",
        "# Times Played White",
        "# Times Played Black",
        "Correct Homework",
        "Incorrect Homework",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)

    total_games = df["Total Wins"] + df["Total Losses"] + df["Total Ties"]
    win_rate = df["Total Wins"].divide(total_games.replace(0, pd.NA)).fillna(0)

    total_homework = df["Correct Homework"] + df["Incorrect Homework"]
    homework_score = df["Correct Homework"].divide(
        total_homework.replace(0, pd.NA)
    ).fillna(0)

    df["rating"] = ((win_weight * win_rate) + (homework_weight * homework_score)).round(3)
    df["color_diff"] = (
        df["# Times Played White"] - df["# Times Played Black"]
    )

    df = df.sort_values(by=["rating", "color_diff"], ascending=[False, True]).reset_index(
        drop=True
    )
    return df


def evaluate_color_penalty(
    player_diff: float, opponent_diff: float, player_as_white: bool
) -> float:
    """Measure the impact on colour balance for a hypothetical assignment."""
    if player_as_white:
        player_future_diff = player_diff + 1
        opponent_future_diff = opponent_diff - 1
    else:
        player_future_diff = player_diff - 1
        opponent_future_diff = opponent_diff + 1

    return abs(player_future_diff) + abs(opponent_future_diff)


def select_pairings(
    sorted_players: pd.DataFrame,
    rng: random.Random,
    student_ids: Optional[List[int]] = None,
    recent_opponents: Optional[Dict[frozenset[int], int]] = None,
    rematch_window: int = 2,
    avoid_recent_rematches: bool = True,
    bye_counts: Optional[Dict[int, int]] = None,
    rotate_byes: bool = True,
) -> Tuple[List[Tuple[int, int]], List[int]]:
    """Generate pairings while respecting rating proximity, rematches, and colour balance."""
    available_indices = list(range(len(sorted_players)))
    matches: List[Tuple[int, int]] = []
    unpaired_indices: List[int] = []
    recent_opponents = recent_opponents or {}
    bye_counts = bye_counts or {}

    if len(available_indices) % 2 == 1:
        if rotate_byes and student_ids:
            def bye_priority(index: int) -> Tuple[int, float]:
                student_id = student_ids[index]
                return (
                    int(bye_counts.get(student_id, 0)),
                    float(sorted_players.at[index, "rating"]),
                )

            bye_index = min(available_indices, key=bye_priority)
            available_indices.remove(bye_index)
            unpaired_indices.append(bye_index)
        else:
            lowest_index = available_indices.pop()
            unpaired_indices.append(lowest_index)

    while available_indices:
        current_index = available_indices.pop(0)

        if not available_indices:
            unpaired_indices.append(current_index)
            break

        candidate_pool = available_indices[:5]
        candidate_options = []

        for opponent_index in candidate_pool:
            player_diff = sorted_players.at[current_index, "color_diff"]
            opponent_diff = sorted_players.at[opponent_index, "color_diff"]
            rating_gap = abs(
                float(sorted_players.at[current_index, "rating"])
                - float(sorted_players.at[opponent_index, "rating"])
            )

            white_penalty = evaluate_color_penalty(player_diff, opponent_diff, True)
            black_penalty = evaluate_color_penalty(player_diff, opponent_diff, False)

            if white_penalty == black_penalty:
                chosen_role = rng.choice(["player_white", "player_black"])
                color_penalty = white_penalty
            elif white_penalty < black_penalty:
                chosen_role = "player_white"
                color_penalty = white_penalty
            else:
                chosen_role = "player_black"
                color_penalty = black_penalty

            rematch_penalty = 0.0
            if avoid_recent_rematches and student_ids and rematch_window > 0:
                student_a = student_ids[current_index]
                student_b = student_ids[opponent_index]
                rounds_since = recent_opponents.get(frozenset({student_a, student_b}))
                if rounds_since is not None and rounds_since <= rematch_window:
                    rematch_penalty = 1000.0 / max(rounds_since, 1)

            penalty = (rating_gap * 2.0) + color_penalty + rematch_penalty

            candidate_options.append((opponent_index, penalty, chosen_role))

        if not candidate_options:
            unpaired_indices.append(current_index)
            continue

        lowest_penalty = min(option[1] for option in candidate_options)
        best_candidates = [
            option for option in candidate_options if option[1] == lowest_penalty
        ]
        opponent_index, _, chosen_role = rng.choice(best_candidates)

        if chosen_role == "player_white":
            white_index, black_index = current_index, opponent_index
        else:
            white_index, black_index = opponent_index, current_index

        matches.append((white_index, black_index))
        available_indices.remove(opponent_index)

    return matches, unpaired_indices


OUTPUT_COLUMNS = [
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


def build_output_rows(
    sorted_players: pd.DataFrame,
    matches: List[Tuple[int, int]],
    unpaired_indices: List[int],
) -> pd.DataFrame:
    """Convert pairings into a DataFrame ready for CSV export."""
    rows: List[dict] = []

    def empty_row() -> Dict[str, str]:
        return {column: "" for column in OUTPUT_COLUMNS}

    for white_idx, black_idx in matches:
        row = empty_row()
        row.update(
            {
                "White Player": sorted_players.at[white_idx, "Student Name"],
                "White Player Strength": f"{sorted_players.at[white_idx, 'rating']:.3f}",
                "Black Player": sorted_players.at[black_idx, "Student Name"],
                "Black Player Strength": f"{sorted_players.at[black_idx, 'rating']:.3f}",
            }
        )
        rows.append(row)

    for index in unpaired_indices:
        row = empty_row()
        row.update(
            {
                "White Player": sorted_players.at[index, "Student Name"],
                "White Player Strength": f"{sorted_players.at[index, 'rating']:.3f}",
            }
        )
        rows.append(row)

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def generate_pairings(
    input_csv: Path,
    output_csv: Path,
    seed: Optional[int] = None,
    win_weight: float = 0.7,
    homework_weight: float = 0.3,
) -> Dict[str, Optional[str]]:
    """Run the pairing pipeline and return summary statistics."""
    rng = random.Random(seed)

    normalized_win_weight, normalized_homework_weight = normalize_weights(
        win_weight, homework_weight
    )
    players = load_and_prepare_players(
        input_csv, normalized_win_weight, normalized_homework_weight
    )
    matches, unpaired_indices = select_pairings(players, rng)
    output_df = build_output_rows(players, matches, unpaired_indices)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_csv(output_csv, index=False)

    total_players = len(players)
    match_count = len(matches)

    unpaired_name: Optional[str]
    unpaired_rating: Optional[float]
    if unpaired_indices:
        bye_index = min(unpaired_indices, key=lambda idx: players.at[idx, "rating"])
        unpaired_name = players.at[bye_index, "Student Name"]
        unpaired_rating = players.at[bye_index, "rating"]
    else:
        unpaired_name = None
        unpaired_rating = None

    return {
        "total_players": total_players,
        "matches": match_count,
        "unpaired_name": unpaired_name,
        "unpaired_rating": unpaired_rating,
        "win_weight": normalized_win_weight,
        "homework_weight": normalized_homework_weight,
    }
