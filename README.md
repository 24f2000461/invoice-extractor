# Invoice Extraction Service

## Run locally
```
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```
Test:
```
curl -X POST http://localhost:8000/extract -H "Content-Type: application/json" \
  -d '{"text":"Vendor: Acme-42 Industries Ltd.\nTotal Due: $4523.75\nPayment Due Date: 2026-03-15"}'
```

## Get a public URL (pick one)

### Option A — Render.com (free, permanent URL, ~3 min)
1. Push `app.py` + `requirements.txt` to a new GitHub repo.
2. Go to render.com -> New -> Web Service -> connect the repo.
3. Build command: `pip install -r requirements.txt`
   Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Deploy. Your endpoint will be `https://<your-app>.onrender.com/extract`.

### Option B — Railway.app (free, similar to Render)
1. railway.app -> New Project -> Deploy from GitHub repo.
2. It auto-detects Python; set the start command to
   `uvicorn app:app --host 0.0.0.0 --port $PORT`.
3. Endpoint: `https://<your-app>.up.railway.app/extract`.

### Option C — ngrok (fastest, temporary, good for grading right now)
On your own machine (with the two files above):
```
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000 &
ngrok http 8000
```
ngrok prints a URL like `https://abcd1234.ngrok-free.app`.
Your submission URL is `https://abcd1234.ngrok-free.app/extract`.
(Keep the terminal open — closing it kills the tunnel.)
