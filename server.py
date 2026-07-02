#!/usr/bin/env python3
import hashlib
import html
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MUSICAU_DB_PATH", ROOT / "waitlist.db"))
HOST = os.environ.get("MUSICAU_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("MUSICAU_PORT", "4173")))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("MUSICAU_RATE_WINDOW_SECONDS", "600"))
RATE_LIMIT_MAX = int(os.environ.get("MUSICAU_RATE_LIMIT_MAX", "5"))

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CONTACT_TYPES = {"phone", "discord", "reddit", "telegram", "instagram", "x", "other"}
REFERRAL_SOURCES = {"", "reddit", "friend", "google", "discord", "youtube", "other"}

rate_limit_hits = {}


def init_db():
  DB_PATH.parent.mkdir(parents=True, exist_ok=True)
  with sqlite3.connect(DB_PATH) as conn:
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS waitlist_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        contact_methods TEXT NOT NULL DEFAULT '[]',
        referral_source TEXT,
        feature_request TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        ip_hash TEXT
      )
      """
    )
    conn.commit()


def clean_text(value, max_length):
  if not isinstance(value, str):
    return ""
  value = CONTROL_RE.sub("", value).strip()
  value = re.sub(r"\s+", " ", value)
  value = html.escape(value, quote=False)
  return value[:max_length]


def clean_multiline(value, max_length):
  if not isinstance(value, str):
    return ""
  value = CONTROL_RE.sub("", value).strip()
  value = re.sub(r"\n{3,}", "\n\n", value)
  value = html.escape(value, quote=False)
  return value[:max_length]


def client_ip(handler):
  cf_ip = handler.headers.get("CF-Connecting-IP", "").strip()
  forwarded = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
  return cf_ip or forwarded or handler.client_address[0]


def ip_hash(ip):
  salt = os.environ.get("MUSICAU_IP_HASH_SALT", "musicau-local")
  return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()


def is_rate_limited(ip):
  now = time.time()
  hits = [hit for hit in rate_limit_hits.get(ip, []) if now - hit < RATE_LIMIT_WINDOW_SECONDS]

  if len(hits) >= RATE_LIMIT_MAX:
    rate_limit_hits[ip] = hits
    return True

  hits.append(now)
  rate_limit_hits[ip] = hits
  return False


def validate_payload(payload):
  if not isinstance(payload, dict):
    return None, "Invalid request body."

  if clean_text(payload.get("website", ""), 120):
    return None, "Submission could not be accepted."

  email = clean_text(payload.get("email", ""), 254).lower()
  if not email:
    return None, "Email is required."
  if not EMAIL_RE.match(email):
    return None, "Please enter a valid email address."

  contacts = []
  raw_contacts = payload.get("contacts", [])
  if raw_contacts is None:
    raw_contacts = []
  if not isinstance(raw_contacts, list):
    return None, "Contact methods must be a list."
  if len(raw_contacts) > 4:
    return None, "Please include no more than four contact methods."

  for item in raw_contacts:
    if not isinstance(item, dict):
      return None, "Invalid contact method."
    contact_type = clean_text(item.get("type", ""), 24)
    contact_value = clean_text(item.get("value", ""), 120)
    if not contact_value:
      continue
    if contact_type not in CONTACT_TYPES:
      return None, "Invalid contact method."
    contacts.append({"type": contact_type, "value": contact_value})

  referral_source = clean_text(payload.get("referralSource", ""), 40).lower()
  if referral_source not in REFERRAL_SOURCES:
    return None, "Invalid referral source."

  feature_request = clean_multiline(payload.get("featureRequest", ""), 600)

  return {
    "email": email,
    "contacts": contacts,
    "referral_source": referral_source,
    "feature_request": feature_request,
  }, None


def save_submission(data, ip):
  now = datetime.now(timezone.utc).isoformat()
  with sqlite3.connect(DB_PATH) as conn:
    conn.execute(
      """
      INSERT INTO waitlist_submissions (
        email, contact_methods, referral_source, feature_request, created_at, updated_at, ip_hash
      )
      VALUES (?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(email) DO UPDATE SET
        contact_methods = excluded.contact_methods,
        referral_source = excluded.referral_source,
        feature_request = excluded.feature_request,
        updated_at = excluded.updated_at,
        ip_hash = excluded.ip_hash
      """,
      (
        data["email"],
        json.dumps(data["contacts"], ensure_ascii=True),
        data["referral_source"] or None,
        data["feature_request"] or None,
        now,
        now,
        ip_hash(ip),
      ),
    )
    conn.commit()


def waitlist_count():
  with sqlite3.connect(DB_PATH) as conn:
    row = conn.execute("SELECT COUNT(*) FROM waitlist_submissions").fetchone()
    return int(row[0])


class MusicAUHandler(SimpleHTTPRequestHandler):
  server_version = "MusicAUWaitlist/1.0"

  def __init__(self, *args, **kwargs):
    super().__init__(*args, directory=str(ROOT), **kwargs)

  def send_json(self, status, payload):
    body = json.dumps(payload).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Cache-Control", "no-store")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def read_json_body(self):
    content_type = self.headers.get("Content-Type", "")
    if "application/json" not in content_type:
      return None, "Content-Type must be application/json."

    try:
      length = int(self.headers.get("Content-Length", "0"))
    except ValueError:
      return None, "Invalid request length."

    if length <= 0 or length > 12_000:
      return None, "Invalid request length."

    try:
      return json.loads(self.rfile.read(length).decode("utf-8")), None
    except json.JSONDecodeError:
      return None, "Invalid JSON."

  def do_GET(self):
    parsed = urlparse(self.path)
    if parsed.path == "/api/waitlist/count":
      self.send_json(HTTPStatus.OK, {"count": waitlist_count()})
      return
    return super().do_GET()

  def do_POST(self):
    parsed = urlparse(self.path)
    if parsed.path != "/api/waitlist":
      self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
      return

    ip = client_ip(self)
    if is_rate_limited(ip):
      self.send_json(
        HTTPStatus.TOO_MANY_REQUESTS,
        {"error": "Too many attempts. Please wait a few minutes and try again."},
      )
      return

    payload, body_error = self.read_json_body()
    if body_error:
      self.send_json(HTTPStatus.BAD_REQUEST, {"error": body_error})
      return

    data, validation_error = validate_payload(payload)
    if validation_error:
      self.send_json(HTTPStatus.BAD_REQUEST, {"error": validation_error})
      return

    try:
      save_submission(data, ip)
    except sqlite3.Error:
      self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not save submission."})
      return

    self.send_json(HTTPStatus.CREATED, {"ok": True, "count": waitlist_count()})


if __name__ == "__main__":
  init_db()
  server = ThreadingHTTPServer((HOST, PORT), MusicAUHandler)
  print(f"MusicAU site running at http://{HOST}:{PORT}")
  print(f"Waitlist database: {DB_PATH}")
  server.serve_forever()
