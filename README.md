# Share Sanket app

## Open Share Sanket

https://koturiharish-collab.github.io/corporate-actions.yml/

Open this same link on a phone, tablet, or computer. The site and alert history are public, so anyone with the link can view them.

Share Sanket is for information only. It is not a platform for buying or selling securities and does not provide investment advice or recommendations. The scrolling notice is shown at the top of the app.

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

## Automatic order alerts

`order_alerts.py` checks NSE and BSE corporate announcements every five minutes on weekdays. Order-win announcements are added to the existing **Orders** folder in `alerts.json`; the app publishes a new version when the data changes.

Each order alert keeps all fields returned by the exchange, the full announcement text, an official filing link, and best-effort text extracted from the filing PDF. Yahoo Finance can add a timestamped snapshot of price, market capitalization, P/E, and other available company data. If a filing does not disclose a client, order value, or execution term, or a market-data field is unavailable, the app shows that clearly instead of estimating it. The order-to-market-cap percentage is calculated only when both figures are available in INR.

The Big, Medium, and Small subfolders are based only on disclosed order value: Big is ₹100 crore or more, Medium is ₹10 crore to under ₹100 crore, and Small is under ₹10 crore. Alerts without a disclosed value remain visible in the main Orders folder and are not assigned a size band.

Exchange filings are the primary source. Yahoo Finance data is supplemental and may be delayed, missing, or changed by its provider. For full disclosure details, open the linked NSE or BSE filing. The order workflow updates the app and does not send duplicate Telegram messages.

## Automatic corporate actions

`corporate_actions.py` now adds every newly detected NSE split, bonus issue, or buyback to `alerts.json` as well as sending the Telegram message. Each Share Sanket alert keeps all source fields from the NSE record in its **Details** section; unavailable fields display as “Not available.”

The app refreshes `alerts.json` every 60 seconds when served from a website. Both scheduled workflows save changes to `alerts.json` and publish them to the same GitHub Pages link.

Keep the Telegram bot token and chat ID in GitHub Actions Secrets named `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Do not put these values in `index.html` or `alerts.json`.

