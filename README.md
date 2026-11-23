# Share Sanket app

## Open Share Sanket

https://koturiharish-collab.github.io/corporate-actions.yml/

Open this same link on a phone, tablet, or computer. The app interface requires an approved account; the alert feed file remains publicly readable on GitHub Pages.

Share Sanket is for information only. It is not a platform for buying or selling securities and does not provide investment advice or recommendations. The scrolling notice is shown at the top of the app.

App files:

- `index.html` plus `firebase-members.js` provide the app and approval-only sign-in.
- `firestore.rules` contains the membership security rules; publish them in Firebase Console before sign-up.
- alerts.json contains the alert feed and remains publicly readable on GitHub Pages.

## Run it on your computer

To use sign-in locally, serve the folder over HTTP (double-clicking the file uses `file://` and Firebase sign-in will not work):

    python -m http.server 8000

Then open http://localhost:8000 in your browser.

## Put it online

Publish `index.html`, `firebase-members.js`, and `alerts.json` together on your static host (GitHub Pages, Netlify, Vercel, or Cloudflare Pages). Keep the same app URL.

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

`order_alerts.py` checks NSE and BSE corporate announcements every five minutes on weekdays from the main GitHub Actions workflow. Order-win announcements are added to the existing **Orders** folder in `alerts.json`; the app publishes a new version when the data changes.

Each order alert keeps all fields returned by the exchange, the full announcement text, an official filing link, and best-effort text extracted from the filing PDF. Yahoo Finance can add a timestamped snapshot of price, market capitalization, P/E, and other available company data. If a filing does not disclose a client, order value, or execution term, or a market-data field is unavailable, the app shows that clearly instead of estimating it. The order-to-market-cap percentage is calculated only when both figures are available in INR.

The Big, Medium, and Small subfolders are based only on disclosed order value: Big is ₹100 crore or more, Medium is ₹10 crore to under ₹100 crore, and Small is under ₹10 crore. Alerts without a disclosed value remain visible in the main Orders folder and are not assigned a size band.

Exchange filings are the primary source. Yahoo Finance data is supplemental and may be delayed, missing, or changed by its provider. For full disclosure details, open the linked NSE or BSE filing. The order workflow updates the app and does not send duplicate Telegram messages.

## Automatic corporate actions

`corporate_actions.py` now adds every newly detected NSE split, bonus issue, or buyback to `alerts.json` as well as sending the Telegram message. Each Share Sanket alert keeps all source fields from the NSE record in its **Details** section; unavailable fields display as “Not available.”

The app refreshes `alerts.json` every 60 seconds when served from a website. The main GitHub Actions workflow runs corporate-action scans every 30 minutes and order scans every five minutes on weekdays, then saves and publishes changed data to the same GitHub Pages link. A separate manual workflow remains available for an on-demand order scan.

Keep the Telegram bot token and chat ID in GitHub Actions Secrets named `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Do not put these values in `index.html` or `alerts.json`.

## 30-member registration and approval

Firebase Authentication uses email and password. New users create a request that starts as `pending`. The app stays locked until the admin approves the request. The admin approval screen has 30 member places; the admin account is separate.

Before publishing the updated app:

1. In Firebase Authentication settings, add `koturiharish-collab.github.io` to Authorized domains so the existing GitHub Pages link can sign users in. A local `file://` copy cannot sign in; use the HTTPS app link.
2. Open Firestore Database > Rules, replace the rules with the contents of `firestore.rules`, then publish them. Without these rules, sign-up and approvals are denied.
3. Publish `index.html` and `firebase-members.js` to the existing GitHub Pages repository and keep them together.
4. Open the live app and request membership using the admin's email. Copy the account ID shown on the pending screen.
5. In Firestore Data, create collection `admins`, use that account ID as the document ID, and add field `role` with string value `admin`. This console-only step designates the first admin; app users cannot grant themselves admin access.
6. Sign out and back in. The admin should see **Member approvals** and can approve or reject requests.

Note: the login gate controls the app interface, but `alerts.json` remains publicly readable at the GitHub Pages link, consistent with the app's public alert feed.