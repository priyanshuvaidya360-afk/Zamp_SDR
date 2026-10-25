# Signal frontend

The Next.js app for Signal. It covers upload → map columns → prospect list → live run view → review queue → dashboard. See the [main README](../README.md) for what the project does, how to run it and how to deploy it.

```bash
npm install
npm run dev     # http://localhost:3000, talks to the API at NEXT_PUBLIC_API_URL (default http://localhost:8000)
```

Before changing code, read [AGENTS.md](AGENTS.md): Next.js 16 differs from older versions. `npx tsc --noEmit` and `npm run lint` must pass.
