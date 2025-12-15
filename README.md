Chess Match Selector & Web App
================================

Overview
--------
- Calculates player strength and colour balance from `Student_Information.csv`.
- Generates fair next-round pairings while keeping colours balanced.
- Provides both a command-line script and a Flask web interface for uploads.
- Web UI includes quick links to the input template, sample output, and GitHub repository.
- Inputs should be in this format as a .csv file: https://docs.google.com/spreadsheets/d/1kJKOxY_5oYmAcgvMtz_e9llXeYifauULxCitCE9vAQM/edit?usp=sharing 
- Output now includes a <code>Who Won</code> column plus homework correct/incorrect counts so you can record each round before feeding it back into the master sheet.
- The app is currently on Render at: https://chess-match-selector.onrender.com/

How to Use
--------------------
- Make a copy of the example inputs and delete the example data.
- Format inputs in a spreadsheet or Excel in the exact columns as the example. 
- If you don't have specific values (ie homework, wins, ties, etc.) leave those cells blank.
- Export the file as a .cvs file
- Use the Render web-app above or run it locally.

Complete Loop Workflow
----------------------
1. Upload the latest `Student_Information.csv` on the main page, optionally tweak the win/homework weights, and download the generated `next_matches.csv`.
2. During/after the round, fill in the result and homework columns for each pairing:
   - `Who Won`: enter `W` (White), `B` (Black), `T` (Tie), or leave blank if no result is available. `Bye` rows can use `W`.
   - `White Homework Correct/Incorrect` and `Black Homework Correct/Incorrect`: enter numeric counts, leaving blanks for zero.
3. Visit `/update` ("Update Student Information" link in the UI) and upload the current `Student_Information.csv` alongside the completed `next_matches.csv`.
4. Download the refreshed master sheet and use it to generate the next round.

Command-Line Usage
------------------
- Install dependencies: `pip install -r requirements.txt`.
- Run the selector: `python chess_match_selector.py --input Student_Information.csv --output next_matches.csv`.
- Optional flags:
  - `--seed 123` for repeatable random pairing.
  - `--win-weight 0.7` and `--homework-weight 0.3` to rebalance the rating calculation (values are normalised automatically).

Flask Web App (Local)
---------------------
- Export `FLASK_APP=app.py` (if needed) and install dependencies.
- Start with `flask run` or `python app.py`.
- Visit `http://localhost:5000`, upload `Student_Information.csv`, adjust the weighting inputs if desired, then download `next_matches.csv`. The app shows a summary including the weights used.
- Use the "Update Student Information" link to open the second page where you can upload the filled `next_matches.csv` together with the current student sheet. The app validates the `Who Won` field (W/B/T/blank), increments win/loss/tie totals, colour history (including bye rounds), and homework counts, and then provides a download link for the refreshed CSV.

`next_matches.csv` header
-------------------------

```
White Player,White Player Strength,Black Player,Black Player Strength,Who Won,White Homework Correct,White Homework Incorrect,Black Homework Correct,Black Homework Incorrect
```

Use `W` for white wins, `B` for black wins, `T` for ties, and leave blank if the result is not recorded yet.

Render Deployment
-----------------
1. Push this repository to GitHub.
2. Create a new Render Web Service, choose the repo, and select the Free plan.
3. Render uses:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
4. Deploy and use the hosted upload form to generate pairings online.
