# Deploying the HedgeWise demo (private GitHub repo + Streamlit Community Cloud)

You end up with a link like `https://hedgewise-demo.streamlit.app` that opens the dashboard in any browser: on the presentation laptop, on your phone in an interview, or for a jury member. It takes about 20 minutes the first time. Everything below is free.

The app only reads the CSV files in `data/`. It never downloads anything when it starts. To show newer prices, refresh the data on your laptop and push again (step 5).

---

## 1. Before you start (once)

1. **A GitHub account.** If you don't have one, sign up at <https://github.com/signup>. Your student email is fine.
2. **Git is already set up** in `~/hedgewise`: every task was committed. Check with:
   ```bash
   cd ~/hedgewise
   git status          # should say "nothing to commit, working tree clean"
   git log --oneline   # list of commits
   ```
   If `git status` lists changed files, commit them first: `git add -A && git commit -m "Before deploy"`.
3. **Check what will be uploaded.** These are excluded on purpose (see `.gitignore`): `.venv/`, `backup_v02/`, `hedgewise_v0.3.zip`, and `data/forwards.csv`. Forward prices from Refinitiv or Bloomberg are licensed, so never put them in the repo. To see exactly which files will go up:
   ```bash
   git ls-files
   ```

## 2. Create a private repository on GitHub

1. Go to <https://github.com/new>.
2. **Repository name:** `hedgewise`.
3. Choose **Private**.
4. Do **not** tick "Add a README", ".gitignore" or "license". The repo must start empty.
5. Click **Create repository**. Keep the page open: it shows the repo URL, for example `https://github.com/<your-username>/hedgewise.git`.

## 3. Push your code (Terminal)

The easiest way to log in is the GitHub CLI:

```bash
brew install gh          # if you don't have it yet (needs Homebrew)
gh auth login            # choose: GitHub.com -> HTTPS -> Login with a web browser; follow the code in the browser
```

Then, replacing `<your-username>`:

```bash
cd ~/hedgewise
git branch -M main
git remote add origin https://github.com/<your-username>/hedgewise.git
git push -u origin main
```

Refresh the GitHub page: you should see `app.py`, `hedgewise_model.py`, `data/`, and so on.

> Without the GitHub CLI: create a token at GitHub → Settings → Developer settings → Personal access tokens → Fine-grained token, with access to the `hedgewise` repo and **Contents: Read and write**. Use the token as the password when `git push` asks for one.

## 4. Deploy on Streamlit Community Cloud

1. Go to <https://share.streamlit.io> and click **Continue with GitHub**. Sign in with the same GitHub account.
2. When GitHub asks for permissions, allow Streamlit access to **private repositories**. Without it, your private repo won't appear in the list.
3. Click **Create app** (top right) → **Deploy a public app from GitHub** / **"Yup, I have an app"**. The wording changes now and then; pick the option that deploys from an existing GitHub repo.
4. Fill in:
   * **Repository:** `<your-username>/hedgewise`
   * **Branch:** `main`
   * **Main file path:** `app.py`
   * **App URL:** choose something short, for example `hedgewise-demo`
5. Click **Advanced settings** → **Python version: 3.12**. Leave secrets empty and click **Save**.
6. Click **Deploy**. The first build installs `requirements.txt` and takes 3–5 minutes. You can watch the log on the right.
7. When it's done the dashboard opens. Test it: change the decision date, download the PDF report in EN and NL, and upload `data/company_example.csv`.

### Who can see it
* Because the repo is private, the app is **private** too: only people you invite can open it. Click **Share** (top right of the running app) → **Invite viewers** by email. Invite your teammates, and the jury or coaches when needed.
* To make it open to anyone with the link (handy for the pitch, so nobody needs to log in), use **Share → Make this app public**. The code stays private; only the app is public. Do this only once you're happy that the app contains no confidential pilot data. Uploaded files are processed in memory and not stored, but the data in `data/` is visible through the app.
* Free accounts can have only one private app at a time.

## 5. Updating the app later

Every `git push` to `main` redeploys the app automatically, within about a minute.

```bash
cd ~/hedgewise
source .venv/bin/activate
python download_data.py          # refresh prices (about 5 minutes)
python -m pytest -q              # tests still pass?
git add -A && git commit -m "Refresh price data"
git push
```

## 6. If something goes wrong

| Symptom | Fix |
|---|---|
| The repo doesn't appear on share.streamlit.io | Settings (top right on share.streamlit.io) → Linked accounts → GitHub → grant access to private repos, then try again |
| Build fails on a package | Open "Manage app" → logs. Usually a version conflict: check `requirements.txt` and choose Python 3.12 in Advanced settings |
| `FileNotFoundError: data/gas_ttf.csv` | The data files weren't pushed. Run `git ls-files data/`; if they're missing: `git add data/*.csv && git commit -m "Add data" && git push` |
| The app is slow or "Oh no" on 10,000 scenarios | The free tier has about 1 GB of memory. Use 2,000–5,000 scenarios in the sidebar for live demos |
| The app went to sleep | Free apps sleep after a few days without visits. Open the link and click "Yes, get this app back up"; it takes about 30 seconds. Open it once on the morning of the pitch |
| The demo must work without internet | Run it locally: `cd ~/hedgewise && source .venv/bin/activate && streamlit run app.py` |

## Notes
* **Data licence:** power prices come from Energy-Charts (CC BY 4.0, source: Bundesnetzagentur | SMARD.de). The app shows this attribution. Gas prices are the Yahoo `TTF=F` front-month. Fine for a pitch demo; for a commercial product, switch to ENTSO-E and licensed forward data.
* **Don't deploy pilot data:** keep real company bills out of the repo. Upload them in the app during the meeting instead.
