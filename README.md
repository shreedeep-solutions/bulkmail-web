# BulkMail Pro — phone & web version (v1.0.0)
Shreedeep Solutions. Sends each member their own bill PDF through Gmail — from a phone or any browser, no PC needed.
The desktop BulkMail Pro (v3.2) stays as it is; both can be used.

**Address:** https://bulkmail.shreedeepsolutions.com · **Who:** partners only (only Gmail accounts listed as "test users" in the Google project `bulkmail-shreedeep` can sign in).

## How it works
- Sign in with Google (the app never sees the Gmail password). Permission: *send email* + *see email dates/recipients* (to count today's use). It cannot read email content.
- **Several Gmail accounts**, used in order. Each has a "switch at" number (default 450). Before sending, the app reads each account's **Sent** folder to see how many recipients it reached in the last 24 h (hand-sent mail counts too). Near the limit → next account. If Gmail refuses anyway → next account, same email again (nobody gets two).
- **Replies always go to the main account** (first in the list) — setting in Accounts.
- A Google sign-in lasts ~1 hour. If it runs out while sending, sending pauses: tap **Connect … & continue**.
- PDFs, member list, templates and history stay **on the device** (browser storage). Nothing is uploaded except the emails themselves.

## Daily use (phone)
1. **Accounts** → *Add Gmail account* (main account first). Add the others. Set "switch at" if needed.
2. **Recipients** → *Add PDFs* (one bill each) or *Split one PDF* (all bills in one file). Then *Import Excel / CSV* (member list with flat no., name, email) → *Find each member's bill in the PDFs*.
3. **Compose** → subject + message with fields like `{Member Name}`, `{Flat No}`. Save as template.
4. **Send** → check the list → *Send test to myself* → *Start sending*. Keep the screen on.
5. Install: Chrome menu → *Add to Home screen* (or Accounts → *Install app*).

## Bills from Solutions4Society
S4S opens this page and hands over the bills (see `S4S-INTEGRATION.md`). A card *Bills from Solutions4Society* appears on the Send tab; nothing is sent until you press **Send … bills now**. Progress is reported back to S4S. Your own Recipients list is not touched.

## Setup (one time)
### Google Cloud (done 8 Nov 2026)
Project `bulkmail-shreedeep` → Gmail API enabled → Google Auth Platform: External, **Testing**, scopes `gmail.send` + `gmail.metadata`, test users = every sending Gmail → Web client with JavaScript origins:
`https://bulkmail.shreedeepsolutions.com`, `https://shreedeep-solutions.github.io`, `https://sas.shreedeepsolutions.com`, `https://s4s.shreedeepsolutions.com`.
Client ID (public, in `index.html`): `327826726135-d2l1rt2ufvv0gjccp061rhrf9ss7ot2g.apps.googleusercontent.com`.
**Adding a new sending Gmail later:** add it under *Audience → Test users* first, otherwise Google blocks its sign-in.
Google shows "Google hasn't verified this app" — tap *Advanced → Go to BulkMail Pro (unsafe)*; it's your own app.

### Hosting (GitHub Pages)
1. Public repo `shreedeep-solutions/bulkmail-web` → upload everything in this folder (keep `lib/`, `icons/`, `CNAME`).
2. Settings → Pages → Deploy from branch `main` / root.
3. Namecheap → shreedeepsolutions.com → Advanced DNS: change the **`bulkmail`** record (it pointed to the old Render site) to **CNAME `bulkmail` → `shreedeep-solutions.github.io.`**
4. GitHub → Settings → Pages: custom domain `bulkmail.shreedeepsolutions.com` → tick **Enforce HTTPS** when offered.

### Updating
Replace the files in the repo. Phones pick up the new version the next time the app is opened online.

## Files
`index.html` (the app) · `sw.js` (offline/quick start) · `manifest.json` + `icons/` (installable) · `lib/` (Excel + PDF libraries, kept here so the app needs no other website) · `CNAME`.

## Limits
- ~500 recipients / 24 h per Gmail account (Gmail's rule for all sending). Add more accounts for more.
- 25 MB per email (Gmail).
- Phone browsers may stop work when the screen locks — keep it on while sending.
- Testing mode allows up to 100 test users — plenty.
