# Stock Sentinel — Backend (FastAPI on Render)

This directory contains the core Stock Sentinel FastAPI backend application, PostgreSQL database migrations, Jinja2 server-rendered views, background scheduler, and AI intelligence pipeline.

---

## Deployment on Render

### Method 1: Automatic Blueprint (Recommended)
1. In the [Render Dashboard](https://dashboard.render.com), click **New +** $\rightarrow$ **Blueprint**.
2. Connect your `Stock-Sentinel` repository. Render will automatically detect `render.yaml` at the root.
3. Supply your environment secrets when prompted.

### Method 2: Manual Web Service Setup
1. In the Render Dashboard, click **New +** $\rightarrow$ **Web Service**.
2. Connect your GitHub repository.
3. Configure the following service settings:
   - **Name**: `stock-sentinel`
   - **Region**: Closest to your Neon PostgreSQL instance (e.g., Frankfurt / Oregon / Ohio)
   - **Root Directory**: `backend`
   - **Runtime**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### Environment Variables
Configure the following in the Render **Environment** tab:

| Variable | Description |
| :--- | :--- |
| `NEON_DATABASE_URL` or `DATABASE_URL` | Neon Serverless PostgreSQL connection string |
| `SECRET_KEY` | High-entropy string for session signing (Render can auto-generate) |
| `CRON_SECRET` | Shared bearer token for triggering `/api/run-workflow` |
| `OPENROUTER_API_KEY` | OpenRouter API key for LLM intelligence summaries |
| `ENVIRONMENT` | Set to `production` |
| `PYTHON_VERSION` | `3.12.0` |

---

## Local Development

```bash
# Navigate to backend
cd backend

# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn main:app --reload --port 5000

# Run test suite
pytest
```
