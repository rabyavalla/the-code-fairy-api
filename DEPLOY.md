# Deploy The Code Fairy Astrology API to Railway

## What This Is
A Python API that calculates real birth charts using both tropical (Western) and sidereal (Vedic) astrology. It powers the chart calculations in The Code Fairy app.

## Deploy to Railway (5 minutes, free tier)

### 1. Create a Railway account
Go to [railway.app](https://railway.app) and sign up (free, no credit card needed).

### 2. Create a new project
1. Click **"New Project"**
2. Select **"Deploy from GitHub Repo"** (or "Empty Project" → "Add Service")
3. If using GitHub:
   - Push this `the-code-fairy-api` folder to a GitHub repo
   - Connect it to Railway
4. If using CLI:
   ```bash
   npm install -g @railway/cli
   railway login
   cd the-code-fairy-api
   railway init
   railway up
   ```

### 3. Set environment variables
In Railway dashboard → your service → **Variables**:
```
PORT=8000
```

### 4. Get your API URL
Railway will give you a URL like:
```
https://the-code-fairy-api-production-xxxx.up.railway.app
```

### 5. Connect to the app
Open the app's `App.js` and find `API_BASE_URL` — replace it with your Railway URL.

## API Endpoints

### POST /chart
Calculate a birth chart.
```json
{
  "name": "Jane",
  "year": 1995,
  "month": 3,
  "day": 21,
  "hour": 14,
  "minute": 30,
  "city": "Los Angeles",
  "country": "US"
}
```

Returns both tropical and sidereal charts with all planets, signs, degrees, houses, and retrograde status.

### GET /transits
Get current planetary positions right now. No parameters needed.

### GET /health
Health check — returns `{"status": "ok"}`.

## Tech Details
- **Framework**: FastAPI (Python)
- **Astrology Engine**: Kerykeion 5.x (uses Swiss Ephemeris under the hood)
- **Sidereal Mode**: Lahiri ayanamsa (most widely used in Vedic astrology)
- **Cost**: Railway free tier gives you 500 hours/month — plenty for development
