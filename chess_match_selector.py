import argparse
import random
from pathlib import Path
from typing import List, Tuple

import pandas as pd


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments for input and output file locations."""
    parser = argparse.ArgumentParser(
        description="Create balanced next-round chess pairings from player data."
    )
    parser.add_argument(
        "--input",
        default="Student_Information.csv",
        help="Path to the source player CSV file.",
    )
    parser.add_argument(
        "--output",
        default="next_matches.csv",
        help="Path for the generated pairings CSV file.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed to make pairing selection reproducible.",
    )
    return parser.parse_args()


def load_and_prepare_players(csv_path: Path) -> pd.DataFrame:
    """Load the CSV, normalise numeric columns, and compute derived metrics."""
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()  # Clean up headers that may include stray spaces.

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
        # Ensure counts are numeric, defaulting missing or malformed values to zero.
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)

    # Compute the win rate, guarding against divisions by zero.
    total_games = (
        df["Total Wins"] + df["Total Losses"] + df["Total Ties"]
    )
    win_rate = df["Total Wins"].divide(total_games.replace(0, pd.NA)).fillna(0)

    # Compute the homework success rate with similar zero checks.
    total_homework = df["Correct Homework"] + df["Incorrect Homework"]
    homework_score = df["Correct Homework"].divide(
        total_homework.replace(0, pd.NA)
    ).fillna(0)

    # Aggregate the strength rating and keep three decimal places.
    df["rating"] = ((0.7 * win_rate) + (0.3 * homework_score)).round(3)

    # Positive values mean more games as white, negative means more games as black.
    df["color_diff"] = (
        df["# Times Played White"] - df["# Times Played Black"]
    )

    # Order players by strength and then by colour balance preference.
    df = df.sort_values(
        by=["rating", "color_diff"], ascending=[False, True]
    ).reset_index(drop=True)

    return df


def evaluate_color_penalty(
    player_diff: float, opponent_diff: float, player_as_white: bool
) -> float:
    """Measure how much a colour assignment impacts overall colour balance."""
    if player_as_white:
        player_future_diff = player_diff + 1
        opponent_future_diff = opponent_diff - 1
    else:
        player_future_diff = player_diff - 1
        opponent_future_diff = opponent_diff + 1

    # Lower combined absolute difference is better for long-term colour balance.
    return abs(player_future_diff) + abs(opponent_future_diff)


def select_pairings(sorted_players: pd.DataFrame, rng: random.Random) -> Tuple[List[Tuple[int, int]], List[int]]:
    """Generate pairings while respecting rating proximity and colour balancing."""
    available_indices = list(range(len(sorted_players)))
    matches: List[Tuple[int, int]] = []

    unpaired_indices: List[int] = []
    if len(available_indices) % 2 == 1:
        # Reserve the lowest-rated player so they receive the bye if needed.
        lowest_index = available_indices.pop()
        unpaired_indices.append(lowest_index)

    while available_indices:
        current_index = available_indices.pop(0)

        if not available_indices:
            # No one left to pair with, so this player becomes the bye.
            unpaired_indices.append(current_index)
            break

        candidate_pool = available_indices[:5]
        candidate_options = []

        for opponent_index in candidate_pool:
            player_diff = sorted_players.at[current_index, "color_diff"]
            opponent_diff = sorted_players.at[opponent_index, "color_diff"]

            # Evaluate both possible colour assignments for this pairing.
            white_penalty = evaluate_color_penalty(player_diff, opponent_diff, True)
            black_penalty = evaluate_color_penalty(player_diff, opponent_diff, False)

            if white_penalty == black_penalty:
                chosen_role = rng.choice(["player_white", "player_black"])
                penalty = white_penalty
            elif white_penalty < black_penalty:
                chosen_role = "player_white"
                penalty = white_penalty
            else:
                chosen_role = "player_black"
                penalty = black_penalty

            candidate_options.append(
                (opponent_index, penalty, chosen_role)
            )

        if not candidate_options:
            # Should not happen due to the candidate pool logic, but guard just in case.
            unpaired_indices.append(current_index)
            continue

        # Prioritise pairings that minimise colour imbalance, then break ties randomly.
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


def build_output_rows(
    sorted_players: pd.DataFrame,
    matches: List[Tuple[int, int]],
    unpaired_indices: List[int],
) -> pd.DataFrame:
    """Convert pairings into a DataFrame ready for CSV export."""
    rows: List[dict] = []

    for white_idx, black_idx in matches:
        rows.append(
            {
                "White Player": sorted_players.at[white_idx, "Student Name"],
                "White Player Strength": f"{sorted_players.at[white_idx, 'rating']:.3f}",
                "Black Player": sorted_players.at[black_idx, "Student Name"],
                "Black Player Strength": f"{sorted_players.at[black_idx, 'rating']:.3f}",
            }
        )

    for index in unpaired_indices:
        rows.append(
            {
                "White Player": sorted_players.at[index, "Student Name"],
                "White Player Strength": f"{sorted_players.at[index, 'rating']:.3f}",
                "Black Player": "",
                "Black Player Strength": "",
            }
        )

    return pd.DataFrame(rows)


def main() -> None:
    """Entrypoint that ties reading, pairing, and exporting together."""
    args = parse_arguments()

    input_path = Path(args.input)
    output_path = Path(args.output)

    rng = random.Random(args.seed)

    players = load_and_prepare_players(input_path)

    matches, unpaired_indices = select_pairings(players, rng)
    output_df = build_output_rows(players, matches, unpaired_indices)
    output_df.to_csv(output_path, index=False)

    total_players = len(players)
    match_count = len(matches)
    if unpaired_indices:
        # Only the lowest-rated player should remain; fall back to the smallest rating if needed.
        bye_index = min(unpaired_indices, key=lambda idx: players.at[idx, "rating"])
        bye_name = players.at[bye_index, "Student Name"]
        bye_rating = players.at[bye_index, "rating"]
        print(f"Processed {total_players} players.")
        print(f"Created {match_count} matches.")
        print(f"Unpaired: {bye_name} (rating {bye_rating:.3f})")
    else:
        print(f"Processed {total_players} players.")
        print(f"Created {match_count} matches.")
        print("Unpaired: None")

    print(f"Exported {output_path.name} successfully.")


if __name__ == "__main__":
    main()
