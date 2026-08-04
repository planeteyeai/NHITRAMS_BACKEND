/**
 * =============================================================================
 * NHIT — Google Sheets ↔ Django Google Sheet Sync
 * =============================================================================
 *
 * Automatically syncs ONE edited data row to:
 *   POST {API_BASE_URL}/api/google-sheet-sync/
 *
 * SETUP
 * -----
 * 1. Open your Google Sheet → Extensions → Apps Script.
 * 2. Delete any default code and paste this entire file. Save.
 * 3. Set Script Properties:
 *      Project Settings (gear) → Script properties → Add script property
 *        Key:   API_BASE_URL
 *        Value: https://xxxx.ngrok-free.app   (no trailing slash)
 *      Optional:
 *        Key:   SYNC_API_KEY
 *        Value: <shared secret if you add API-key auth later>
 * 4. Create an INSTALLABLE onEdit trigger (simple triggers cannot call UrlFetchApp):
 *      Triggers (clock icon) → Add Trigger
 *        Function:        onEdit
 *        Event source:    From spreadsheet
 *        Event type:      On edit
 *        Failure notify:  Notify me immediately (recommended)
 * 5. Local Django + ngrok:
 *      python manage.py runserver
 *      ngrok http 8000
 *      Copy the https URL into API_BASE_URL (Script property).
 * 6. Authorize when prompted (first run) — allow UrlFetch / Spreadsheet access.
 *
 * HEADER ROW
 * ----------
 * Row 1 must contain human-readable headers. Columns are discovered dynamically
 * via normalizeHeader() — never hardcode column indexes.
 *
 * Meta columns (written back by this script; edits to them alone do not sync):
 *   Sync ID | Status | Last Synced | Error Message
 *
 * =============================================================================
 */

/** Sheet-only columns that must never trigger a sync when written back. */
var META_FIELD_KEYS = {
  sync_id: true,
  status: true,
  last_synced: true,
  error_message: true
};

/** Fields that must be present (non-empty) before calling the API. */
var REQUIRED_PAYLOAD_KEYS = ['project_name', 'distress_type', 'latitude', 'longitude'];

/** Cache header map briefly to avoid re-scanning on every cell write burst. */
var HEADER_CACHE_KEY = 'NHIT_HEADER_MAP_V1';
var HEADER_CACHE_TTL_SECONDS = 30;

/** Recursion-guard property (set while writing Status / Sync ID / etc.). */
var WRITE_BACK_LOCK_KEY = 'NHIT_WRITE_BACK_IN_PROGRESS';

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * Installable onEdit trigger handler.
 * Syncs only the edited data row; skips header, meta-only edits, and write-backs.
 *
 * @param {Object} e Spreadsheet onEdit event object.
 */
function onEdit(e) {
  try {
    if (!e || !e.range) {
      Logger.log('onEdit: missing event/range — ignore');
      return;
    }

    if (isWriteBackInProgress_()) {
      Logger.log('onEdit: write-back in progress — skip to prevent loop');
      return;
    }

    var sheet = e.range.getSheet();
    var row = e.range.getRow();

    // Never sync the header row.
    if (row === 1) {
      Logger.log('onEdit: header row edited — skip');
      // Header changed → clear cached column map.
      CacheService.getScriptCache().remove(HEADER_CACHE_KEY);
      return;
    }

    var colIndexes = getColumnIndexes(sheet);
    if (!colIndexes || Object.keys(colIndexes).length === 0) {
      Logger.log('onEdit: no headers detected on row 1');
      return;
    }

    // If the user only edited meta columns, do not call the API.
    if (isMetaOnlyEdit_(e, colIndexes)) {
      Logger.log('onEdit: meta-only edit on row ' + row + ' — skip');
      return;
    }

    Logger.log('onEdit: syncing row ' + row + ' on sheet "' + sheet.getName() + '"');
    syncRow(sheet, row, colIndexes);
  } catch (err) {
    Logger.log('onEdit unexpected error: ' + err);
    // Best-effort error write if we can resolve columns.
    try {
      if (e && e.range) {
        var sh = e.range.getSheet();
        var r = e.range.getRow();
        if (r > 1) {
          writeError(sh, r, getColumnIndexes(sh), String(err));
        }
      }
    } catch (ignore) {
      // Swallow secondary failures.
    }
  }
}

/**
 * Manual helper: sync the currently selected row (for testing without an edit).
 * Run from the Apps Script editor: select syncActiveRow → Run.
 */
function syncActiveRow() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var row = sheet.getActiveCell().getRow();
  if (row === 1) {
    SpreadsheetApp.getUi().alert('Select a data row (not the header).');
    return;
  }
  var colIndexes = getColumnIndexes(sheet);
  syncRow(sheet, row, colIndexes);
}

// ---------------------------------------------------------------------------
// Core sync flow
// ---------------------------------------------------------------------------

/**
 * Build payload for one row, validate required fields, call API, write result.
 *
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} row 1-based row number
 * @param {Object=} colIndexes Optional precomputed map from getColumnIndexes()
 */
function syncRow(sheet, row, colIndexes) {
  colIndexes = colIndexes || getColumnIndexes(sheet);

  var payload = buildPayload(sheet, row, colIndexes);
  Logger.log('syncRow: payload for row ' + row + ' = ' + JSON.stringify(payload));
  Logger.log('syncRow: outgoing sync_id = ' + JSON.stringify(payload.sync_id));

  if (!hasRequiredFields_(payload)) {
    Logger.log('syncRow: incomplete required fields — Status=Incomplete');
    writeIncomplete_(sheet, row, colIndexes);
    return;
  }

  try {
    var result = sendToApi(payload);
    Logger.log(
      'syncRow: API HTTP ' + result.httpCode + ' body=' + result.rawBody
    );

    // CRITICAL: persist sync_id whenever the API returns one — including
    // processing errors (HTTP 400 with success:false). Otherwise the sheet
    // keeps a blank Sync ID and every edit creates a NEW staging row.
    var returnedSyncId =
      result.body && result.body.sync_id ? String(result.body.sync_id) : null;
    Logger.log('syncRow: returned sync_id = ' + JSON.stringify(returnedSyncId));

    if (result.ok && result.body && result.body.success === true && returnedSyncId) {
      writeSuccess(
        sheet,
        row,
        colIndexes,
        returnedSyncId,
        result.body.action || ''
      );
      return;
    }

    var message =
      (result.body && result.body.message) ||
      result.rawBody ||
      ('HTTP ' + result.httpCode);
    writeError(sheet, row, colIndexes, String(message), returnedSyncId);
  } catch (err) {
    Logger.log('syncRow: request failed — ' + err);
    writeError(sheet, row, colIndexes, String(err), null);
  }
}

/**
 * Read one sheet row into a snake_case JSON object for the Django API.
 * Empty cells become null. Strings are trimmed. Meta-only sheet columns
 * (last_synced, error_message) are not sent to the API.
 *
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} row
 * @param {Object} colIndexes map of snake_case key → 1-based column index
 * @return {Object}
 */
function buildPayload(sheet, row, colIndexes) {
  var payload = {};
  var keys = Object.keys(colIndexes);

  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];

    // Sheet-only columns — never include in API body.
    if (key === 'last_synced' || key === 'error_message') {
      continue;
    }

    var col = colIndexes[key];
    var raw = sheet.getRange(row, col).getValue();
    payload[key] = normalizeCellValue_(raw);
  }

  return payload;
}

/**
 * POST payload to Django: {API_BASE_URL}/api/google-sheet-sync/
 *
 * @param {Object} payload
 * @return {{ok:boolean, httpCode:number, body:Object|null, rawBody:string}}
 */
function sendToApi(payload) {
  var props = PropertiesService.getScriptProperties();
  var baseUrl = (props.getProperty('API_BASE_URL') || '').replace(/\/$/, '');

  if (!baseUrl) {
    throw new Error(
      'Script property API_BASE_URL is not set. ' +
      'Example: https://xxxx.ngrok-free.app'
    );
  }

  var url = baseUrl + '/api/google-sheet-sync/';
  Logger.log('sendToApi: POST ' + url);

  var headers = {
    'Content-Type': 'application/json'
  };

  // Optional shared secret (ignored by current Django API unless you add it).
  var apiKey = props.getProperty('SYNC_API_KEY');
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  }

  // ngrok free tier sometimes requires this browser-like header.
  if (baseUrl.indexOf('ngrok') !== -1) {
    headers['ngrok-skip-browser-warning'] = 'true';
  }

  var response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: headers,
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
    followRedirects: true
  });

  var httpCode = response.getResponseCode();
  var rawBody = response.getContentText() || '';
  var body = null;

  try {
    body = rawBody ? JSON.parse(rawBody) : null;
  } catch (parseErr) {
    Logger.log('sendToApi: non-JSON response: ' + rawBody);
  }

  return {
    ok: httpCode >= 200 && httpCode < 300,
    httpCode: httpCode,
    body: body,
    rawBody: rawBody
  };
}

// ---------------------------------------------------------------------------
// Sheet write-back (with recursion guard)
// ---------------------------------------------------------------------------

/**
 * On successful create/update: write Sync ID, Status=Synced, Last Synced=now,
 * and clear Error Message.
 *
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} row
 * @param {Object} colIndexes
 * @param {string} syncId UUID from API
 * @param {string} action "created" | "updated" (logged only)
 */
function writeSuccess(sheet, row, colIndexes, syncId, action) {
  Logger.log(
    'writeSuccess: row=' + row + ' sync_id=' + syncId + ' action=' + action
  );
  beginWriteBack_();
  try {
    setCellIfMapped_(sheet, row, colIndexes, 'sync_id', syncId);
    setCellIfMapped_(sheet, row, colIndexes, 'status', 'Synced');
    setCellIfMapped_(sheet, row, colIndexes, 'last_synced', new Date());
    setCellIfMapped_(sheet, row, colIndexes, 'error_message', '');
    SpreadsheetApp.flush();
  } finally {
    endWriteBack_();
  }
}

/**
 * On API / network / processing failure: Status=Error and Error Message.
 * If the API already created a staging row and returned sync_id, write it
 * back so the next edit updates that row instead of creating a duplicate.
 *
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} row
 * @param {Object} colIndexes
 * @param {string} message
 * @param {string|null} syncId Optional UUID from API error/success body
 */
function writeError(sheet, row, colIndexes, message, syncId) {
  Logger.log(
    'writeError: row=' + row + ' message=' + message +
    ' sync_id=' + JSON.stringify(syncId || null)
  );
  beginWriteBack_();
  try {
    if (syncId) {
      setCellIfMapped_(sheet, row, colIndexes, 'sync_id', syncId);
    }
    setCellIfMapped_(sheet, row, colIndexes, 'status', 'Error');
    setCellIfMapped_(sheet, row, colIndexes, 'error_message', message);
    setCellIfMapped_(sheet, row, colIndexes, 'last_synced', new Date());
    SpreadsheetApp.flush();
  } finally {
    endWriteBack_();
  }
}

/**
 * Required fields missing: Status=Incomplete (no API call).
 *
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} row
 * @param {Object} colIndexes
 */
function writeIncomplete_(sheet, row, colIndexes) {
  beginWriteBack_();
  try {
    setCellIfMapped_(sheet, row, colIndexes, 'status', 'Incomplete');
    setCellIfMapped_(
      sheet,
      row,
      colIndexes,
      'error_message',
      'Missing required field(s): Project Name, Distress Type, Latitude, Longitude'
    );
    SpreadsheetApp.flush();
  } finally {
    endWriteBack_();
  }
}

// ---------------------------------------------------------------------------
/* Header discovery & normalization */
// ---------------------------------------------------------------------------

/**
 * Build a map of snake_case field name → 1-based column index from header row 1.
 * Results are cached briefly for performance.
 *
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @return {Object.<string, number>}
 */
function getColumnIndexes(sheet) {
  var cache = CacheService.getScriptCache();
  var cacheKey = HEADER_CACHE_KEY + ':' + sheet.getSheetId();
  var cached = cache.get(cacheKey);
  if (cached) {
    try {
      return JSON.parse(cached);
    } catch (e) {
      // Fall through and rebuild.
    }
  }

  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var map = {};

  for (var i = 0; i < headers.length; i++) {
    var raw = headers[i];
    if (raw === null || raw === undefined || String(raw).trim() === '') {
      continue;
    }
    var key = normalizeHeader(raw);
    if (!key) {
      continue;
    }
    // First occurrence wins if duplicate headers exist.
    if (map[key] === undefined) {
      map[key] = i + 1; // 1-based
    }
  }

  Logger.log('getColumnIndexes: ' + JSON.stringify(map));
  cache.put(cacheKey, JSON.stringify(map), HEADER_CACHE_TTL_SECONDS);
  return map;
}

/**
 * Convert a human header into snake_case API field name.
 *
 * Examples:
 *   "Project Name"                    → project_name
 *   "Image URL"                       → image_url
 *   "Chainage Start"                  → chainage_start
 *   "Faulting (Stepping) in Cracks"   → faulting_stepping_in_cracks
 *   "Polished surface/ Glazing"       → polished_surface_glazing
 *   "Popout (Small Hole)"             → popout_small_hole
 *   "Last Synced"                     → last_synced
 *
 * @param {string} header
 * @return {string}
 */
function normalizeHeader(header) {
  return String(header)
    .toLowerCase()
    .replace(/[\/\\]/g, ' ') // slashes → space
    .replace(/[()]/g, ' ') // drop parentheses, keep inner words
    .replace(/[^a-z0-9]+/g, '_') // non-alnum → underscore
    .replace(/^_+|_+$/g, '') // trim underscores
    .replace(/_+/g, '_'); // collapse repeats
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Normalize a single cell value for JSON:
 * - blank → null
 * - string → trimmed (empty after trim → null)
 * - Date → yyyy-MM-dd
 * - numbers / booleans left as-is
 *
 * @param {*} value
 * @return {*}
 */
function normalizeCellValue_(value) {
  if (value === null || value === undefined) {
    return null;
  }

  if (Object.prototype.toString.call(value) === '[object Date]') {
    return Utilities.formatDate(
      value,
      Session.getScriptTimeZone(),
      'yyyy-MM-dd'
    );
  }

  if (typeof value === 'string') {
    var trimmed = value.trim();
    return trimmed === '' ? null : trimmed;
  }

  // Spreadsheet empty numeric cells sometimes come through as ''.
  if (value === '') {
    return null;
  }

  return value;
}

/**
 * True when project_name, distress_type, latitude, longitude are all present.
 *
 * @param {Object} payload
 * @return {boolean}
 */
function hasRequiredFields_(payload) {
  for (var i = 0; i < REQUIRED_PAYLOAD_KEYS.length; i++) {
    var key = REQUIRED_PAYLOAD_KEYS[i];
    var value = payload[key];
    if (value === null || value === undefined || value === '') {
      Logger.log('hasRequiredFields_: missing ' + key);
      return false;
    }
  }
  return true;
}

/**
 * True if every edited column in the event maps to a meta field only.
 *
 * @param {Object} e
 * @param {Object} colIndexes
 * @return {boolean}
 */
function isMetaOnlyEdit_(e, colIndexes) {
  var startCol = e.range.getColumn();
  var numCols = e.range.getNumColumns();
  var reverse = {};

  var keys = Object.keys(colIndexes);
  for (var i = 0; i < keys.length; i++) {
    reverse[colIndexes[keys[i]]] = keys[i];
  }

  for (var offset = 0; offset < numCols; offset++) {
    var col = startCol + offset;
    var field = reverse[col];
    // Unknown column (no header) — treat as data so we still sync.
    if (!field) {
      return false;
    }
    if (!META_FIELD_KEYS[field]) {
      return false;
    }
  }
  return true;
}

/**
 * Write a value into a mapped column if that header exists.
 *
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} row
 * @param {Object} colIndexes
 * @param {string} key snake_case field
 * @param {*} value
 */
function setCellIfMapped_(sheet, row, colIndexes, key, value) {
  var col = colIndexes[key];
  if (!col) {
    Logger.log('setCellIfMapped_: column "' + key + '" not found — skip');
    return;
  }
  sheet.getRange(row, col).setValue(value);
}

/**
 * Recursion guard — mark that Apps Script is writing meta cells.
 */
function beginWriteBack_() {
  PropertiesService.getScriptProperties().setProperty(
    WRITE_BACK_LOCK_KEY,
    String(Date.now())
  );
}

/**
 * Clear the recursion guard after write-back completes.
 */
function endWriteBack_() {
  PropertiesService.getScriptProperties().deleteProperty(WRITE_BACK_LOCK_KEY);
}

/**
 * @return {boolean}
 */
function isWriteBackInProgress_() {
  var flag = PropertiesService.getScriptProperties().getProperty(
    WRITE_BACK_LOCK_KEY
  );
  if (!flag) {
    return false;
  }
  // Auto-expire stale locks (e.g. script killed mid-write) after 15s.
  var started = parseInt(flag, 10);
  if (!isNaN(started) && Date.now() - started > 15000) {
    endWriteBack_();
    return false;
  }
  return true;
}

/**
 * One-shot connectivity test using Script Properties API_BASE_URL.
 * Run from the editor: select testApiConnection → Run → View → Logs.
 */
function testApiConnection() {
  var sample = {
    sync_id: null,
    status: null,
    latitude: 19.991,
    longitude: 73.789,
    chainage_start: 100,
    chainage_end: null,
    project_name: 'Agra Bypass Road',
    distress_type: 'Crack',
    direction: null,
    date: '2026-07-17',
    image_url: null
  };

  var result = sendToApi(sample);
  Logger.log('testApiConnection: HTTP ' + result.httpCode);
  Logger.log('testApiConnection: body ' + result.rawBody);
}
