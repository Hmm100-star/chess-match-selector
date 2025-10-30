import argparse
from pathlib import Path

from pairing_logic import generate_pairings


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
    parser.add_argument(
        "--win-weight",
        type=float,
        default=0.7,
        help="Weight assigned to win/loss performance when computing strength.",
    )
    parser.add_argument(
        "--homework-weight",
        type=float,
        default=0.3,
        help="Weight assigned to homework accuracy when computing strength.",
    )
    return parser.parse_args()


def main() -> None:
    """Entrypoint that ties reading, pairing, and exporting together."""
    args = parse_arguments()

    input_path = Path(args.input)
    output_path = Path(args.output)

    summary = generate_pairings(
        input_path,
        output_path,
        seed=args.seed,
        win_weight=args.win_weight,
        homework_weight=args.homework_weight,
    )

    print(f"Processed {summary['total_players']} players.")
    print(f"Created {summary['matches']} matches.")
    if summary["unpaired_name"]:
        print(
            f"Unpaired: {summary['unpaired_name']} (rating {summary['unpaired_rating']:.3f})"
        )
    else:
        print("Unpaired: None")

    print(
        "Weights used -> Wins: "
        f"{summary['win_weight']:.3f}, Homework: {summary['homework_weight']:.3f}"
    )
    print(f"Exported {output_path.name} successfully.")


if __name__ == "__main__":
    main()
