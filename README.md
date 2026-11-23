# Share Sanket app

Two files:

- `index.html` is the app. You do not need to edit it.
- `alerts.json` is the data. Edit this to add folders and alerts.

## Run it on your computer

Opening `index.html` by double-click shows the app with built-in sample data. To make it read your own `alerts.json`, serve the folder:

    python -m http.server 8000

Then open http://localhost:8000 in your browser.

## Put it online

Upload both files to any static host (Netlify, Vercel, GitHub Pages, Cloudflare Pages). Keep them in the same folder.

## Data format

Open "Data format" in the app's sidebar. It lists every field, which ones are required, and a copyable example.

Short version:

- `folders`: a list of `{ id, name, subfolders: [{ id, name }] }`. Two levels only.
- `alerts`: a list of `{ id, time, folder, subfolder, symbol, company, title, price, change_pct, details, link }`.
- `details` is a list of `{ label, value, source }`. A missing `value` shows as "Not available".

## Rules for your data

Facts only: what was filed, what crossed, how big. No buy or sell calls, no targets, no scores. Always fill in the source.

## Next steps

1. Have your bots write alerts into `alerts.json` (or into a database, with a small API that returns this same shape).
2. Replace the sample alerts.
3. Set `"sample": false` to hide the sample-data note.

## Automatic corporate actions

`corporate_actions.py` now adds every newly detected NSE split, bonus issue, or buyback to `alerts.json` as well as sending the Telegram message. Each Share Sanket alert keeps all source fields from the NSE record in its **Details** section; unavailable fields display as “Not available.”

The app refreshes `alerts.json` every 60 seconds when served from a website. The workflow saves changes to `alerts.json` in the repository. To make the app accessible on phones and other devices, enable GitHub Pages for this repository and serve the repository root. The public link is available after Pages finishes publishing.

Keep the Telegram bot token and chat ID in GitHub Actions Secrets named `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Do not put these values in `index.html` or `alerts.json`.

