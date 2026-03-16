# Frontend

React + Vite + TypeScript UI for RepoSentry.

## Responsibilities
- authenticate users with Clerk
- list repositories
- trigger scans
- show scan progress and results
- triage findings
- compare scans

## Development

```bash
npm install
npm run dev
```

The frontend expects these environment variables at build/runtime:
- `VITE_CLERK_PUBLISHABLE_KEY`
- `VITE_API_BASE_URL`

For the project-wide setup, see the root `README.md`.
