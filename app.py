from pathlib import Path
from typing import Optional, Union

from flask import Flask, render_template, request, send_file
from werkzeug.utils import secure_filename

from pairing_logic import generate_pairings


BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUT_FILENAME = "next_matches.csv"
SUMMARY_FILE = OUTPUTS_DIR / "summary.txt"

DEFAULT_WIN_WEIGHT = 0.7
DEFAULT_HOMEWORK_WEIGHT = 0.3

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)


def allowed_file(filename: str) -> bool:
    """Allow only CSV uploads for safety."""
    return filename.lower().endswith(".csv")


def load_summary() -> Optional[str]:
    """Read pairing summary from disk if it exists."""
    if SUMMARY_FILE.exists():
        return SUMMARY_FILE.read_text(encoding="utf-8")
    return None


def build_context(**overrides: Union[str, float, bool, None]) -> dict:
    """Assemble template context with sensible defaults."""
    context = {
        "output_exists": (OUTPUTS_DIR / OUTPUT_FILENAME).exists(),
        "summary": load_summary(),
        "win_weight": DEFAULT_WIN_WEIGHT,
        "homework_weight": DEFAULT_HOMEWORK_WEIGHT,
        "error": None,
        "success": None,
    }
    context.update(overrides)
    return context


@app.route("/", methods=["GET"])
def index() -> str:
    """Render the upload form and show pairing summary if available."""
    return render_template("index.html", **build_context())


@app.route("/upload", methods=["POST"])
def upload() -> str:
    """Handle file upload, run pairing logic, and redisplay the form."""
    uploaded_file = request.files.get("file")
    output_file = OUTPUTS_DIR / OUTPUT_FILENAME
    win_weight_raw = request.form.get("win_weight", "").strip()
    homework_weight_raw = request.form.get("homework_weight", "").strip()

    if not uploaded_file or uploaded_file.filename == "":
        return (
            render_template(
                "index.html",
                **build_context(
                    error="Please choose a CSV file to upload.",
                    win_weight=win_weight_raw or DEFAULT_WIN_WEIGHT,
                    homework_weight=homework_weight_raw or DEFAULT_HOMEWORK_WEIGHT,
                ),
            ),
            400,
        )

    filename = secure_filename(uploaded_file.filename)
    if not allowed_file(filename):
        return (
            render_template(
                "index.html",
                **build_context(
                    error="Only .csv files are accepted.",
                    win_weight=win_weight_raw or DEFAULT_WIN_WEIGHT,
                    homework_weight=homework_weight_raw or DEFAULT_HOMEWORK_WEIGHT,
                ),
            ),
            400,
        )

    try:
        win_weight = (
            float(win_weight_raw) if win_weight_raw else DEFAULT_WIN_WEIGHT
        )
    except ValueError:
        return (
            render_template(
                "index.html",
                **build_context(
                    error="Win weight must be a number.",
                    win_weight=win_weight_raw,
                    homework_weight=homework_weight_raw or DEFAULT_HOMEWORK_WEIGHT,
                ),
            ),
            400,
        )

    try:
        homework_weight = (
            float(homework_weight_raw) if homework_weight_raw else DEFAULT_HOMEWORK_WEIGHT
        )
    except ValueError:
        return (
            render_template(
                "index.html",
                **build_context(
                    error="Homework weight must be a number.",
                    win_weight=win_weight_raw or DEFAULT_WIN_WEIGHT,
                    homework_weight=homework_weight_raw,
                ),
            ),
            400,
        )

    if win_weight < 0 or homework_weight < 0:
        return (
            render_template(
                "index.html",
                **build_context(
                    error="Weights must be zero or greater.",
                    win_weight=win_weight_raw,
                    homework_weight=homework_weight_raw,
                ),
            ),
            400,
        )

    saved_path = UPLOADS_DIR / filename
    uploaded_file.save(saved_path)

    try:
        summary_info = generate_pairings(
            saved_path,
            output_file,
            win_weight=win_weight,
            homework_weight=homework_weight,
        )
    except ValueError as exc:
        return (
            render_template(
                "index.html",
                **build_context(
                    error=str(exc),
                    win_weight=win_weight_raw,
                    homework_weight=homework_weight_raw,
                ),
            ),
            400,
        )

    unpaired_section = "None"
    if summary_info["unpaired_name"]:
        unpaired_section = (
            f"{summary_info['unpaired_name']} (rating {summary_info['unpaired_rating']:.3f})"
        )

    summary_text = (
        f"Processed {summary_info['total_players']} players.\n"
        f"Created {summary_info['matches']} matches.\n"
        f"Unpaired: {unpaired_section}\n"
        f"Weights: wins={summary_info['win_weight']:.3f}, homework={summary_info['homework_weight']:.3f}"
    )
    SUMMARY_FILE.write_text(summary_text, encoding="utf-8")

    return render_template(
        "index.html",
        **build_context(
            output_exists=True,
            summary=summary_text,
            success="Pairings generated successfully.",
            win_weight=summary_info["win_weight"],
            homework_weight=summary_info["homework_weight"],
        ),
    )


@app.route("/download", methods=["GET"])
def download():
    """Serve the generated next_matches.csv file."""
    output_file = OUTPUTS_DIR / OUTPUT_FILENAME
    if not output_file.exists():
        return (
            render_template(
                "index.html",
                **build_context(
                    error="No generated matches found. Upload a CSV first.",
                    output_exists=False,
                    summary=None,
                ),
            ),
            404,
        )

    return send_file(output_file, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
