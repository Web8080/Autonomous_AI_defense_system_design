# Defense dashboard

Next.js app for operators: map, assets, alerts, control, simulation viewer.

## Local run

```bash
npm install
npm run dev
```

Open http://localhost:3000. Optional: copy `.env.local.example` to `.env.local` and set `NEXT_PUBLIC_API_URL` if the API is not at http://localhost:8000.

## Docker (local production build)

```bash
docker build -t defense-dashboard --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 .
docker run -p 3000:3000 -e NEXT_PUBLIC_API_URL=http://localhost:8000 defense-dashboard
```

## Deploy to AWS

See [../docs/Dashboard-Deployment.md](../docs/Dashboard-Deployment.md) for ECS, S3/CloudFront, and Amplify placeholders. Set `NEXT_PUBLIC_API_URL` at build time to your API URL; configure CORS on the API for the dashboard origin.
