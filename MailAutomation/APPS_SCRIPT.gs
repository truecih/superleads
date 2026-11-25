/**
 * Generic outreach sender + reply reader — Google Apps Script (runs in the
 * cloud, no PC needs to be on).
 *
 * WHY THIS DESIGN:
 *   Apps Script limits a single execution to ~6 minutes, so we must NOT sleep
 *   between sends inside one run. A 30-minute clock trigger fires, and each
 *   firing sends exactly ONE pending email during business hours, capped at
 *   DAILY_CAP/day. 08:00-18:00 = 20 half-hour slots = 20 emails/day.
 *   A second independent trigger checks the inbox for replies every 4 hours
 *   and records them in a reply tab + flags the source row in the main sheet.
 *
 * ONE-TIME SETUP:
 *  1. Put this script in a standalone Apps Script project signed in as your
 *     sending Google account.
 *  2. Create a Google Sheet named exactly the value of SHEET_NAME below, with
 *     a first tab whose header is:
 *       seq | company | to | subject | body | status | sent_at
 *     (export_for_gsheet.py produces a ready-to-import CSV.)
 *  3. Adjust the CONFIG block below, then run setUp() once and approve the
 *     permission prompt (send mail + Gmail read + Drive access).
 *  4. To stop everything, run stopAll().
 *
 * NOTES:
 *  - Consumer Gmail allows ~100 recipients/day via Apps Script; 20/day is safe.
 *  - "status"/"sent_at" are written after every send, so reruns never duplicate.
 *  - Outside the business-hours window or after the daily cap, a firing does
 *    nothing. All times use the TIMEZONE below.
 */

/* ============================ CONFIG (edit me) ============================ */
var SHEET_NAME = 'Outreach Send Plan';   // exact name of your Google Sheet
var DAILY_CAP = 20;                      // max emails per day
var START_HOUR = 8;                      // send window start
var END_HOUR = 18;                       // send window end (exclusive)
var TIMEZONE = 'GMT+8';                  // Apps Script timezone string
var REPLY_TAB = 'Replies';               // tab created for inbound replies
var REPLY_TAG = 'Reply';                 // column added to main sheet
var REPLY_LOOKBACK_DAYS = 14;            // how far back to scan the inbox
var SEND_TRIGGER_MINUTES = 30;           // clock interval for the send trigger
var REPLY_TRIGGER_HOURS = 4;             // clock interval for the reply trigger
/* ========================================================================= */

function getSpreadsheet_() {
  var files = DriveApp.getFilesByName(SHEET_NAME);
  if (!files.hasNext()) throw new Error('Sheet not found: ' + SHEET_NAME);
  return SpreadsheetApp.open(files.next());
}

function getSheet_() {
  return getSpreadsheet_().getSheets()[0];
}

function headerIndex_(header) {
  var idx = {};
  header.forEach(function (h, i) { idx[String(h).trim()] = i; });
  var required = ['seq', 'company', 'to', 'subject', 'body', 'status', 'sent_at'];
  for (var r = 0; r < required.length; r++) {
    if (idx[required[r]] === undefined) throw new Error('Missing column: ' + required[r]);
  }
  return idx;
}

function countSentToday_(data, idx) {
  var today = Utilities.formatDate(new Date(), TIMEZONE, 'yyyy-MM-dd');
  var n = 0;
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][idx['status']] || '').trim().toLowerCase() !== 'sent') continue;
    var d = data[i][idx['sent_at']];
    var ds = (d instanceof Date)
      ? Utilities.formatDate(d, TIMEZONE, 'yyyy-MM-dd')
      : String(d || '');
    if (ds === today || ds.indexOf(today) === 0) n++;
  }
  return n;
}

/* ============================ setup / stop ============================== */

/** One-time setup: create BOTH triggers (send + reply-read). Idempotent. */
function setUp() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    var fn = t.getHandlerFunction();
    if (fn === 'dailySend' || fn === 'checkReplies') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('dailySend').timeBased().everyMinutes(SEND_TRIGGER_MINUTES).create();
  ScriptApp.newTrigger('checkReplies').timeBased().everyHours(REPLY_TRIGGER_HOURS).create();
  MailApp.getRemainingDailyQuota();       // prompt for send-mail permission
  GmailApp.search('in:inbox', 0, 1);      // prompt for Gmail read permission
  getReplyTab_();                         // make sure the reply tab exists
  Logger.log('Setup done: send every ' + SEND_TRIGGER_MINUTES + ' min (' +
             START_HOUR + '-' + END_HOUR + ', ' + TIMEZONE + ', max ' + DAILY_CAP +
             '/day); replies every ' + REPLY_TRIGGER_HOURS + ' h.');
}

/** Emergency stop: remove all our triggers. */
function stopAll() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    var fn = t.getHandlerFunction();
    if (fn === 'dailySend' || fn === 'checkReplies') ScriptApp.deleteTrigger(t);
  });
  Logger.log('Stopped scheduled sending and reply checking.');
}

/* ============================ send flow ================================= */

/** Each firing sends at most ONE pending email (keeps every run under 6 min). */
function dailySend() {
  var sheet = getSheet_();
  var data = sheet.getDataRange().getValues();
  var idx = headerIndex_(data[0]);

  var now = new Date();
  var shHour = parseInt(Utilities.formatDate(now, TIMEZONE, 'HH'), 10);
  if (shHour < START_HOUR || shHour >= END_HOUR) {
    Logger.log('Outside send window (' + TIMEZONE + ' ' + START_HOUR + '-' + END_HOUR + '), skipping.');
    return;
  }
  if (MailApp.getRemainingDailyQuota() <= 0) {
    Logger.log('Daily mail quota exhausted, resuming tomorrow.');
    return;
  }
  if (countSentToday_(data, idx) >= DAILY_CAP) {
    Logger.log('Daily cap ' + DAILY_CAP + ' reached, skipping.');
    return;
  }

  for (var i = 1; i < data.length; i++) {
    var st = String(data[i][idx['status']] || '').trim().toLowerCase();
    if (st !== '' && st !== 'pending') continue;
    var rec = data[i];
    try {
      MailApp.sendEmail({
        to: String(rec[idx['to']]).trim(),
        subject: String(rec[idx['subject']]),
        body: String(rec[idx['body']])
      });
      sheet.getRange(i + 1, idx['status'] + 1).setValue('sent');
      sheet.getRange(i + 1, idx['sent_at'] + 1).setValue(new Date());
      Logger.log('Sent row ' + (i + 1) + ': ' + String(rec[idx['to']]).trim());
    } catch (e) {
      sheet.getRange(i + 1, idx['status'] + 1).setValue('error');
      sheet.getRange(i + 1, idx['sent_at'] + 1).setValue(String(e));
      Logger.log('Send failed row ' + (i + 1) + ': ' + e);
    }
    break; // one email per firing
  }
}

/* ============================ reply-reading flow ======================== */

function getReplyTab_() {
  var ss = getSpreadsheet_();
  var tabs = ss.getSheets();
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].getName() === REPLY_TAB) return tabs[i];
  }
  var tab = ss.insertSheet(REPLY_TAB);
  tab.appendRow(['message_id', 'Company', 'From', 'Subject',
                 'Received', 'Snippet', 'Source Seq', 'Recorded']);
  return tab;
}

function extractEmail_(s) {
  var text = String(s || '');
  var m = text.match(/<([^>]+)>/);
  return (m ? m[1] : text).trim().toLowerCase();
}

/** Read inbound replies; write them to the reply tab and flag the source row. */
function checkReplies() {
  var main = getSheet_();
  var data = main.getDataRange().getValues();
  var idx = headerIndex_(data[0]);

  // Ensure the main sheet has the reply-flag column (append if missing).
  var replyCol = -1;
  for (var h = 0; h < data[0].length; h++) {
    if (String(data[0][h]).trim() === REPLY_TAG) replyCol = h;
  }
  if (replyCol < 0) {
    replyCol = data[0].length;
    main.getRange(1, replyCol + 1).setValue(REPLY_TAG);
  }

  // Map recipient email -> source row, only for rows we actually mailed.
  var sentMap = {};
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][idx['status']] || '').trim().toLowerCase() !== 'sent') continue;
    var to = String(data[i][idx['to']] || '').trim().toLowerCase();
    if (!to) continue;
    sentMap[to] = {
      row: i + 1,
      company: String(data[i][idx['company']] || ''),
      seq: String(data[i][idx['seq']] || '')
    };
  }

  var replyTab = getReplyTab_();
  var rdata = replyTab.getDataRange().getValues();
  var seen = {};
  for (var r = 1; r < rdata.length; r++) seen[String(rdata[r][0] || '')] = true;

  var threads = GmailApp.search('in:inbox newer_than:' + REPLY_LOOKBACK_DAYS + 'd', 0, 50);
  var added = 0;
  for (var t = 0; t < threads.length; t++) {
    var msgs = threads[t].getMessages();
    for (var m = 0; m < msgs.length; m++) {
      var msg = msgs[m];
      var id = msg.getId();
      if (seen[id]) continue;
      var from = extractEmail_(msg.getFrom());
      var hit = sentMap[from];
      if (!hit) continue;
      var plain = String(msg.getPlainBody() || '');
      var snippet = plain.replace(/\s+/g, ' ').trim().slice(0, 300);
      replyTab.appendRow([id, hit.company, from, msg.getSubject(),
                          msg.getDate(), snippet, hit.seq, new Date()]);
      main.getRange(hit.row, replyCol + 1)
          .setValue('Replied ' + Utilities.formatDate(msg.getDate(), TIMEZONE, 'yyyy-MM-dd'));
      seen[id] = true;
      added++;
    }
  }
  Logger.log('Reply check done: scanned ' + threads.length + ' threads, added ' + added + ' replies.');
}

/** Diagnostic: log status counts and how many were sent today. */
function statusSummary() {
  var data = getSheet_().getDataRange().getValues();
  var idx = headerIndex_(data[0]);
  var counts = {};
  var today = Utilities.formatDate(new Date(), TIMEZONE, 'yyyy-MM-dd');
  var todayN = 0;
  for (var i = 1; i < data.length; i++) {
    var st = String(data[i][idx['status']] || '').trim() || '(pending)';
    counts[st] = (counts[st] || 0) + 1;
    if (st === 'sent') {
      var d = data[i][idx['sent_at']];
      var ds = (d instanceof Date) ? Utilities.formatDate(d, TIMEZONE, 'yyyy-MM-dd') : String(d || '');
      if (ds === today) todayN++;
    }
  }
  Logger.log('Status ' + JSON.stringify(counts) + ' | sent today ' + todayN + '.');
}
