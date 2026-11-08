# Solutions4Society → BulkMail Pro (phone / web) — hand-over
For the S4S "📧 Email through BulkMail" button (Bills). Keep the current desktop link as it is and add the web route:

1. **Desktop first:** if BulkMail desktop answers on `http://127.0.0.1:5000…/api/s4s/hello` (existing code + link code), use it as today.
2. **Otherwise (phone, or PC without BulkMail):** hand the bills to the web app:

```js
function sendViaBulkMailWeb(job) {           // job = {society, items:[...], cc, reply_to}
  var ORIGIN = "https://bulkmail.shreedeepsolutions.com";
  var w = window.open(ORIGIN + "/#s4s", "bulkmail");   // must run inside the button's click handler
  if (!w) { alert("Allow pop-ups for this site to open BulkMail."); return; }
  var sent = false;
  function onMsg(e) {
    if (e.origin !== ORIGIN || !e.data) return;
    if (e.data.type === "bulkmail-ready" && !sent) { sent = true; w.postMessage({ type: "bulkmail-job", job: job }, ORIGIN); }
    if (e.data.type === "bulkmail-progress") {
      // e.data = {total, sent, failed, done, items:[{email, status: pending|sending|sent|failed|stopped, detail}]}
      showProgress(e.data);
      if (e.data.done) window.removeEventListener("message", onMsg);
    }
  }
  window.addEventListener("message", onMsg);
  // if the page was already open/slow: ask again
  var tries = 0, t = setInterval(function () { if (sent || ++tries > 20) return clearInterval(t); try { w.postMessage({ type: "bulkmail-ping" }, ORIGIN); } catch (e) {} }, 500);
}
```

**job.items[i]**: `{ email, name, flat, subject, text, files: [{ name: "Society_Flat_Month.pdf", pdf: <base64 string or ArrayBuffer> }] }` (up to 5 PDFs per member; same email on several items = separate emails here — S4S already groups per member). `cc`, `reply_to` optional (empty reply_to → replies go to the main BulkMail account).

Safety: the web app accepts jobs only from `https://sas.shreedeepsolutions.com`, `https://s4s.shreedeepsolutions.com` and `https://shreedeep-solutions.github.io`, and nothing is sent until a partner presses **Send … bills now** and is signed in with Google. No link code needed for this route.
