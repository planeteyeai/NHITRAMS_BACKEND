# Google Apps Script setup (Google Sheets → Django sync)

Complete script: [`google_apps_script.js`](./google_apps_script.js)

## 1. Local Django + ngrok

```bash
# Terminal 1 — API on local Postgres
python manage.py runserver

# Terminal 2 — public HTTPS tunnel
ngrok http 8000
```

Copy the `https://….ngrok-free.app` URL (no trailing slash).

Quick API check:

```bash
curl -X POST https://YOUR_NGROK_URL/api/google-sheet-sync/ \
  -H "Content-Type: application/json" \
  -H "ngrok-skip-browser-warning: true" \
  -d "{\"sync_id\":null,\"latitude\":19.991,\"longitude\":73.789,\"project_name\":\"Agra Bypass Road\",\"distress_type\":\"Crack\"}"
```

## 2. Sheet headers (row 1)

Use exactly these labels (order can vary; script discovers columns dynamically):

| Sync ID | Status | Last Synced | Error Message | Latitude | Longitude | … | Project Name | Distress Type | … | Image URL |

Headers are converted to snake_case automatically (`Project Name` → `project_name`).

## 3. Install the script

1. Google Sheet → **Extensions → Apps Script**
2. Paste `google_apps_script.js` → **Save**
3. **Project Settings** (gear) → **Script properties** → Add:
   - `API_BASE_URL` = `https://YOUR_NGROK_URL`
4. **Triggers** (clock) → **Add Trigger**:
   - Function: `onEdit`
   - Event source: **From spreadsheet**
   - Event type: **On edit**
5. Authorize when prompted (needs Spreadsheet + external URL access)

> Use an **installable** trigger. A simple `onEdit` cannot call `UrlFetchApp`.

## 4. Test

1. Editor → select `testApiConnection` → **Run** → **Executions / Logs**
2. Or select a data row → run `syncActiveRow`
3. Edit a data cell (e.g. Latitude) → Status should become `Synced` and Sync ID fills in

## Behaviour summary

| Situation | Sheet result |
|-----------|----------------|
| Missing Project Name / Distress Type / Lat / Lng | Status=`Incomplete` (no API call) |
| API success | Sync ID set, Status=`Synced`, Last Synced=now |
| API `success: false` | Status=`Error`, Error Message=API message |
| Network / exception | Status=`Error`, Error Message=exception |
| Edit only Sync ID / Status / Last Synced / Error Message | No API call (loop guard) |

## Postman

Import [`postman_collection.json`](./postman_collection.json). Set `baseUrl` to your ngrok or local host.
