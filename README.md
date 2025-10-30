Chess Match Selector & Web App
================================

Overview
--------
- Calculates player strength and colour balance from `Student_Information.csv`.
- Generates fair next-round pairings while keeping colours balanced.
- Provides both a command-line script and a Flask web interface for uploads.

Command-Line Usage
------------------
- Install dependencies: `pip install -r requirements.txt`.
- Run the selector: `python chess_match_selector.py --input Student_Information.csv --output next_matches.csv`.
- Optional: add `--seed 123` for repeatable random pairing.

Flask Web App (Local)
---------------------
- Export `FLASK_APP=app.py` (if needed) and install dependencies.
- Start with `flask run` or `python app.py`.
- Visit `http://localhost:5000`, upload `Student_Information.csv`, then download `next_matches.csv`.

Render Deployment
-----------------
1. Push this repository to GitHub.
2. Create a new Render Web Service, choose the repo, and select the Free plan.
3. Render uses:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
4. Deploy and use the hosted upload form to generate pairings online.
