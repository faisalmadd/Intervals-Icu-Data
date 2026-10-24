# Intervals.icu → Google Drive + Gemini Coach Sync

Automated pipeline that extracts **all** workout and wellness data from [Intervals.icu](https://intervals.icu), archives it in Google Drive, and powers a custom Gemini running coach skill.

## Features

- 📊 **Complete Data Archive** — Raw `.fit.gz` files, activity summaries, daily wellness metrics
- ☁️ **Google Drive Backup** — Compressed files organized by year, CSVs updated daily
- 🤖 **Gemini Coach Skill** — AI-powered running/trail coaching from your own training data
- ⏰ **Daily Sync** — GitHub Actions cron at 22:00 UTC+8, with manual backfill option
- 💰 **100% Free** — GitHub Actions + Google Drive free tiers

## Quick Start

### 1. Google Cloud Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g. `intervals-sync`)
3. Enable the **Google Drive API** (`APIs & Services` → `Enable APIs` → search "Google Drive API")
4. Go to `IAM & Admin` → `Service Accounts` → `Create Service Account`
5. Name it `intervals-sync`, click through (no roles needed)
6. Click on the service account → `Keys` → `Add Key` → `Create new key` → **JSON**
7. Download the JSON file — you'll paste its contents into GitHub Secrets

### 2. Google Drive Folder

1. In your personal Google Drive, create a folder called `Intervals_Backup`
2. Right-click → `Share` → paste the service account email (from the JSON file, looks like `intervals-sync@your-project.iam.gserviceaccount.com`) → set role to **Editor**
3. Copy the folder ID from the URL: `drive.google.com/drive/folders/` **`<THIS_PART>`**

### 3. Intervals.icu API Key

1. Log into [Intervals.icu](https://intervals.icu)
2. Go to **Settings** → scroll to **Developer Settings**
3. Copy your **API Key**
4. Note your **Athlete ID** (shown in the URL or settings, e.g. `i12345`; you can also use `0`)

### 4. GitHub Secrets

Go to your repo **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret Name | Value |
|-------------|-------|
| `INTERVALS_API_KEY` | Your Intervals.icu API key |
| `INTERVALS_ATHLETE_ID` | Your athlete ID (e.g. `i12345` or `0`) |
| `GDRIVE_SERVICE_ACCOUNT_KEY` | The **entire JSON** contents of the service account key file |
| `GDRIVE_FOLDER_ID` | The Google Drive folder ID from step 2 |

### 5. Run First Sync

1. Go to **Actions** tab → **Intervals.icu Sync** → **Run workflow**
2. Check **"Backfill all history from account inception"** ✅
3. Click **Run workflow**
4. Wait for it to complete (~1-5 minutes depending on history size)

After this, the daily cron will automatically sync new data every night at 22:00 UTC+8.

### 6. Install Gemini Coach Skill (Optional)

Copy the skill to your Gemini/Antigravity config:

```powershell
# Windows
xcopy /E /I skill\intervals-coach "%USERPROFILE%\.gemini\config\skills\intervals-coach"
```

```bash
# macOS/Linux
cp -r skill/intervals-coach ~/.gemini/config/skills/intervals-coach
```

Then ask Gemini things like:
- *"Should I run today?"*
- *"How was my training week?"*
- *"Am I ready for my race on November 15?"*
- *"Analyze my long run from Saturday"*

## Project Structure

```
├── .github/workflows/sync.yml   # GitHub Actions cron workflow
├── src/sync.py                  # Core sync engine
├── data/                        # Git-committed lightweight data mirror
│   ├── activities.csv           # All activities (all sport types)
│   ├── wellness.csv             # Daily health metrics
│   ├── training_status.md       # 30-day running snapshot
│   └── sync_log.md              # Last sync metadata
├── skill/intervals-coach/       # Gemini AI coaching skill
│   └── SKILL.md
├── requirements.txt
└── README.md
```

## Data Schema

### activities.csv
Date, Name, Type, Distance (km), Duration (min), Elevation Gain (m), Avg/Max HR, Avg Pace, GAP, Cadence, TSS, TRIMP, CTL/ATL/TSB, RPE, Feeling, Notes, Laps (JSON), Weather, Activity ID

### wellness.csv
Date, CTL/ATL/TSB, Resting HR, HRV (rMSSD), Sleep Duration/Quality, Readiness, Mood, Motivation, Stress, Fatigue, Weight, Body Fat %

## Cost

| Resource | Monthly Usage | Free Limit | Cost |
|----------|--------------|------------|------|
| GitHub Actions | ~10 min | 2,000 min | **\$0** |
| Google Drive | ~30-50 MB/year | 15 GB | **\$0** |
| Google Drive API | ~30 calls/day | 1B/day | **\$0** |

## License

MIT
