Chess Match Selector & Web App
================================

Overview
--------
- Calculates player strength and colour balance from `Student_Information.csv`.
- Generates fair next-round pairings while keeping colours balanced.
- Provides both a command-line script and a Flask web interface for uploads.
- Inputs should be in this format as a .csv file: https://docs.google.com/spreadsheets/d/1kJKOxY_5oYmAcgvMtz_e9llXeYifauULxCitCE9vAQM/edit?usp=sharing 
- Output will look like: https://docs.google.com/spreadsheets/d/1-yRVcTHes2QIS2x6wMTnXj-ONyKOT50CcB9WxviX3rw/edit?usp=sharing
- The app is currently on Render at: https://chess-match-selector.onrender.com/ 

How to Use
--------------------
- Make a copy of the example inputs and delete the example data.
- Format inputs in a spreadsheet or Excel in the exact columns as the example. 
- If you don't have specific values (ie homework, wins, ties, etc.) leave those cells blank.
- Export the file as a .cvs file
- Use the Render web-app above or run it locally.

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