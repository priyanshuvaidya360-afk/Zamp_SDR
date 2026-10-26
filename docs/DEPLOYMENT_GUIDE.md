# Deployment guide (free, no credit card)

Signal runs on three free services, each set up by importing this GitHub repo. The config files in the repo do the setup; you only paste secrets.

| Part | Host | Config file in the repo |
|---|---|---|
| Database | **Neon** (Postgres) | [`neon.ts`](../neon.ts) |
| Backend (FastAPI) | **Render**, free web service | [`render.yaml`](../render.yaml) |
| Frontend (Next.js) | **Vercel**, Hobby plan | [`frontend/vercel.json`](../frontend/vercel.json) |

Deploy them in this order: the frontend needs the backend's address, and the backend needs the database's.

## 1. Database: Neon

Already done for this project (`soft-boat-20927108`, branch `production`, region `aws-us-east-2`). To do it again from scratch:

```bash
npm i -g neon
neon login                      # in your browser
neon init -y --org-id <org> --project-id <project> --branch production --services postgres
neon deploy                     # applies neon.ts and writes DATABASE_URL + DATABASE_URL_UNPOOLED to .env.local
```

`.env.local` holds the database password. It is git-ignored; keep it that way. You copy its two URLs into Render in the next step.

## 2. Backend: Render

1. Sign in at [render.com](https://render.com) with GitHub.
2. **New → Blueprint** → choose this repo → Render reads `render.yaml` and shows service `zamp-sdr-api` (free plan, region Ohio, next to the database).
3. Fill in the values it asks for:

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | from `.env.local` (the pooled URL, host contains `-pooler`) |
   | `DATABASE_URL_UNPOOLED` | from `.env.local` (the direct URL) |
   | `GROQ_API_KEY`, `GEMINI_API_KEY`, `TAVILY_API_KEY` | the same keys as in `backend/.env` |
   | `EXA_API_KEY` | optional: leave empty |

   CORS needs no input: `render.yaml` already allows the Vercel project `zamp-sdr` (production and preview URLs).
4. **Apply**. The first build takes a few minutes. When it is live, open `https://<your-service>.onrender.com/health`; it should answer `{"ok":true}`. Copy that base URL for step 3.

## 3. Frontend: Vercel

1. Sign in at [vercel.com](https://vercel.com) with GitHub → **Add New → Project** → import this repo.
2. **Root Directory: `frontend`** (click Edit next to it). Vercel then detects Next.js and uses `frontend/vercel.json`.
3. **Project name: `zamp-sdr`** (the default from the repo name). The backend's CORS pattern expects it; if you choose another name, change `CORS_ORIGIN_REGEX` in `render.yaml`.
4. **Environment Variables:** `NEXT_PUBLIC_API_URL` = the Render URL from step 2 (no trailing slash). The build stops with a clear message if it is missing.
5. **Deploy.** Open the site, click **Use the demo CSV** or upload your own, and draft a prospect.

## After deploying

- **Every push to `main` redeploys** both Render and Vercel automatically.
- **Render's free service sleeps after 15 minutes without visits** and takes about 50 seconds to wake. Open the site a few minutes before a demo, or have a free pinger (e.g. cron-job.org) call `/health` every 10 minutes; one always-on service fits inside Render's 750 free hours a month.
- **Free AI quotas are the real limit**, not hosting: Groq's smart model allows about 200k tokens a day (roughly 20 prospects). Each visitor is capped at 20 uploads and 60 prospects an hour (`app/limits.py`), so a shared link can't drain them.
- **A restart mid-run** (deploy, sleep) marks that run failed; open it and click **Retry run**.
- **Changing the backend's address** (new Render service) means updating `NEXT_PUBLIC_API_URL` in Vercel and redeploying, because it is built into the frontend.

## Demo without any of this

`run-demo.ps1` (Windows) or `make dev-api-demo` runs the backend locally in replay mode: no keys, no network, no quota. See the README.
