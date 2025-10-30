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
    return parser.parse_args()


def main() -> None:
    """Entrypoint that ties reading, pairing, and exporting together."""
    args = parse_arguments()

    input_path = Path(args.input)
    output_path = Path(args.output)

    summary = generate_pairings(input_path, output_path, args.seed)

    print(f"Processed {summary['total_players']} players.")
    print(f"Created {summary['matches']} matches.")
    if summary["unpaired_name"]:
        print(
            f"Unpaired: {summary['unpaired_name']} (rating {summary['unpaired_rating']:.3f})"
        )
    else:
        print("Unpaired: None")

    print(f"Exported {output_path.name} successfully.")


if __name__ == "__main__":
    main()
