# MusicAU Landing Page

Static landing page plus a small SQLite-backed waitlist API.

## Run Locally

```bash
python3 server.py
```

Open:

```text
http://127.0.0.1:4173/
```

Submissions are stored in `waitlist.db` by default.

## API

- `POST /api/waitlist` stores a waitlist submission.
- `GET /api/waitlist/count` returns the current waitlist count.

Each submission stores:

- email
- optional contact methods
- referral source
- feature request
- created and updated timestamps

## Environment Variables

- `PORT` or `MUSICAU_PORT`: server port, default `4173`
- `MUSICAU_HOST`: host, default `127.0.0.1`
- `MUSICAU_DB_PATH`: SQLite database path, default `./waitlist.db`
- `MUSICAU_RATE_LIMIT_MAX`: submissions per IP window, default `5`
- `MUSICAU_RATE_WINDOW_SECONDS`: rate window, default `600`
- `MUSICAU_IP_HASH_SALT`: salt used when hashing IP addresses

## Deploy

For a small VPS, run `python3 server.py` behind Nginx/Caddy with HTTPS and set
`MUSICAU_HOST=0.0.0.0`. For production, use a persistent database location via
`MUSICAU_DB_PATH` and set a private `MUSICAU_IP_HASH_SALT`.

The backend validates and sanitizes inputs, includes a honeypot field, rate
limits by IP, and returns JSON success/error responses.
