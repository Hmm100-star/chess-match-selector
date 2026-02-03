Chess Match Selector & Web App
==============================

Overview
--------
- Multi-class, database-backed chess pairing tool with teacher login.
- Stores student totals, attendance, and full round history in a database.
- Generates balanced pairings using win/homework weights and colour balance.
- Records winners/losers, homework counts, and notes directly in the web UI.
- Keeps CSV import/export available for backups or offline workflows.

How to Use (Web)
----------------
1. Visit the app and create the first teacher account (one-time setup).
2. Create a class and add students manually or import `Student_Information.csv`.
3. Start a new round, mark absences, and generate pairings.
4. Record results, homework counts, and notes on the round results page.
5. Export CSVs whenever you need offline copies.

CSV Compatibility
-----------------
The app can import and export the legacy `Student_Information.csv` format. Exports include:
- Student totals and notes.
- Round-specific `next_matches.csv` data.

Local Development
-----------------
- Install dependencies: `pip install -r requirements.txt`.
- Start the app: `python app.py`.
- Visit `http://localhost:5000` and complete the initial teacher setup.

Configuration
-------------
- `SECRET_KEY`: set a secure random value for session security.
- `DATABASE_URL`: optional SQLAlchemy database URL (defaults to `sqlite:///data/chess_match.db`).

Production (PythonAnywhere)
---------------------------
1. Create a virtualenv and install requirements.
2. Set environment variables (`SECRET_KEY`, `DATABASE_URL`).
3. Configure the WSGI file to point to `app:app`.
4. Restart the web app.

Security Notes
--------------
- Passwords are stored as salted hashes.
- Each teacher account owns isolated classes and data.
- All forms use CSRF protection.
