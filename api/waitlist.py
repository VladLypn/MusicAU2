import hashlib
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CONTACT_TYPES = {"phone", "discord", "reddit", "telegram", "instagram", "x", "other"}
REFERRAL_SOURCES = {"", "reddit", "friend", "google", "discord", "youtube", "other"}
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("MUSICAU_RATE_WINDOW_SECONDS", "600"))
RATE_LIMIT_MAX = int(os.environ.get("MUSICAU_RATE_LIMIT_MAX", "5"))


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
  forwarded = handler.headers.get("x-forwarded-for", "").split(",")[0].strip()
  real_ip = handler.headers.get("x-real-ip", "").strip()
  return forwarded or real_ip or "unknown"


def ip_hash(ip):
  salt = os.environ.get("MUSICAU_IP_HASH_SALT", "musicau-vercel")
  return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()


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

  return {
    "email": email,
    "contacts": contacts,
    "referral_source": referral_source or None,
    "feature_request": clean_multiline(payload.get("featureRequest", ""), 600) or None,
  }, None


def supabase_config():
  url = os.environ.get("SUPABASE_URL", "").rstrip("/")
  key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
  if not url or not key:
    raise RuntimeError("Supabase is not configured.")
  return url, key


def supabase_request(path, method="GET", body=None, extra_headers=None):
  base_url, key = supabase_config()
  headers = {
    "apikey": key,
    "Authorization": f"Bearer {key}",
    "Content-Type": "application/json",
    "Accept": "application/json",
  }
  headers.update(extra_headers or {})
  data = None if body is None else json.dumps(body).encode("utf-8")
  request = Request(f"{base_url}/rest/v1/{path}", data=data, headers=headers, method=method)

  try:
    with urlopen(request, timeout=12) as response:
      response_body = response.read().decode("utf-8")
      return response.status, dict(response.headers), response_body
  except HTTPError as error:
    response_body = error.read().decode("utf-8")
    return error.code, dict(error.headers), response_body
  except URLError:
    return 502, {}, json.dumps({"message": "Could not reach Supabase."})


def count_waitlist():
  status, headers, body = supabase_request(
    "waitlist_submissions?select=id",
    extra_headers={"Prefer": "count=exact", "Range": "0-0"},
  )
  if status >= 400:
    raise RuntimeError(read_supabase_error(body))

  content_range = headers.get("Content-Range") or headers.get("content-range") or ""
  if "/" in content_range:
    return int(content_range.rsplit("/", 1)[1])

  data = json.loads(body or "[]")
  return len(data)


def is_rate_limited(ip):
  cutoff = datetime.now(timezone.utc) - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
  query = urlencode(
    {
      "select": "id",
      "ip_hash": f"eq.{ip_hash(ip)}",
      "updated_at": f"gte.{cutoff.isoformat()}",
    }
  )
  status, headers, body = supabase_request(
    f"waitlist_submissions?{query}",
    extra_headers={"Prefer": "count=exact", "Range": "0-0"},
  )
  if status >= 400:
    raise RuntimeError(read_supabase_error(body))

  content_range = headers.get("Content-Range") or headers.get("content-range") or ""
  if "/" in content_range:
    return int(content_range.rsplit("/", 1)[1]) >= RATE_LIMIT_MAX

  return False


def save_submission(data, ip):
  now = datetime.now(timezone.utc).isoformat()
  payload = {
    "email": data["email"],
    "contact_methods": data["contacts"],
    "referral_source": data["referral_source"],
    "feature_request": data["feature_request"],
    "created_at": now,
    "updated_at": now,
    "ip_hash": ip_hash(ip),
  }
  query = urlencode({"on_conflict": "email"})
  status, _, body = supabase_request(
    f"waitlist_submissions?{query}",
    method="POST",
    body=payload,
    extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
  )
  if status >= 400:
    raise RuntimeError(read_supabase_error(body))


def read_supabase_error(body):
  try:
    data = json.loads(body or "{}")
  except json.JSONDecodeError:
    return "Could not save submission."
  return data.get("message") or data.get("error") or "Could not save submission."


class handler(BaseHTTPRequestHandler):
  def send_json(self, status, payload):
    body = json.dumps(payload).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Cache-Control", "no-store")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def read_json_body(self):
    try:
      length = int(self.headers.get("content-length", "0"))
    except ValueError:
      return None, "Invalid request length."

    if length <= 0 or length > 12_000:
      return None, "Invalid request length."

    try:
      return json.loads(self.rfile.read(length).decode("utf-8")), None
    except json.JSONDecodeError:
      return None, "Invalid JSON."

  def do_OPTIONS(self):
    self.send_response(HTTPStatus.NO_CONTENT)
    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
    self.end_headers()

  def do_GET(self):
    try:
      self.send_json(HTTPStatus.OK, {"count": count_waitlist()})
    except RuntimeError as error:
      self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})

  def do_POST(self):
    payload, body_error = self.read_json_body()
    if body_error:
      self.send_json(HTTPStatus.BAD_REQUEST, {"error": body_error})
      return

    data, validation_error = validate_payload(payload)
    if validation_error:
      self.send_json(HTTPStatus.BAD_REQUEST, {"error": validation_error})
      return

    try:
      ip = client_ip(self)
      if is_rate_limited(ip):
        self.send_json(
          HTTPStatus.TOO_MANY_REQUESTS,
          {"error": "Too many attempts. Please wait a few minutes and try again."},
        )
        return
      save_submission(data, ip)
      self.send_json(HTTPStatus.CREATED, {"ok": True, "count": count_waitlist()})
    except RuntimeError as error:
      self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
