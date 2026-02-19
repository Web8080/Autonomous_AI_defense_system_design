# Dashboard: run locally and deploy to AWS

## Run locally

1. **From repo root:**
   ```bash
   cd dashboard
   npm install
   npm run dev
   ```
2. Open **http://localhost:3000**. Log in (dev-token when API is localhost), then use Map, Assets, Alerts, Control, Simulation.
3. **Optional:** Copy `.env.local.example` to `.env.local` and set `NEXT_PUBLIC_API_URL` if the API is not at `http://localhost:8000`.

## Run locally with Docker

Build and run the production image (API URL must be reachable from the browser; for same host use host.docker.internal or the host IP):

```bash
cd dashboard
docker build -t defense-dashboard --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 .
docker run -p 3000:3000 -e NEXT_PUBLIC_API_URL=http://localhost:8000 defense-dashboard
```

Then open http://localhost:3000. On Linux, use the host machine IP instead of `localhost` for `NEXT_PUBLIC_API_URL` if the API runs on the host.

---

## Deploy to AWS (placeholders)

Use one of the options below after replacing placeholders with your account ID, region, and domain. Do not run Terraform or deploy without valid credentials and review.

### Option A: ECS/Fargate (recommended for SSR)

- **Placeholder:** Push the dashboard Docker image to Amazon ECR, create an ECS task definition and service, put an Application Load Balancer (ALB) in front, and set `NEXT_PUBLIC_API_URL` to the API gateway URL (e.g. ALB or API Gateway).
- **Terraform:** Add a module in `infra/terraform/` for: ECR repo, ECS cluster (or use existing), task definition (image from ECR, env `NEXT_PUBLIC_API_URL`), service, ALB, optional Route53/ACM. CORS on the API gateway must allow the dashboard origin (e.g. `https://dashboard.yourdomain.com`).
- **Build/push:** e.g. `aws ecr get-login-password --region REGION | docker login --username AWS --password-stdin ACCOUNT.dkr.ecr.REGION.amazonaws.com`, then `docker build`, `docker tag`, `docker push` to ECR.

### Option B: Static export + S3 + CloudFront

- **Placeholder:** If the dashboard can be fully static (no SSR), run `next build` with `output: 'export'` in `next.config.js`, upload the `out/` directory to an S3 bucket with static hosting, and put CloudFront in front. Set `NEXT_PUBLIC_API_URL` at build time to your API URL.
- **Caveat:** Current app uses client-side auth and API calls; static export works if all data is loaded client-side. If you later add SSR or API routes, use Option A instead.

### Option C: AWS Amplify

- **Placeholder:** Connect the repo to Amplify, set build to `npm ci && npm run build`, output to `.next`. Set env var `NEXT_PUBLIC_API_URL` in Amplify to the API base URL. Amplify hosts the Next.js app and handles HTTPS.

### Env and CORS

- **Build-time:** `NEXT_PUBLIC_API_URL` must point to the API users will call (e.g. `https://api.yourdomain.com`). Set in Amplify/CodeBuild/ECS task def or in `docker build --build-arg`.
- **Backend:** Ensure the API gateway `CORS_ORIGINS` includes the dashboard origin (e.g. `https://dashboard.yourdomain.com`). No trailing slash.

### Terraform placeholder (dashboard)

See `infra/terraform/dashboard.tf` for a minimal placeholder: ECR repo and optional S3 bucket for static assets. Expand with ECS task definition, ALB, and DNS when you deploy.
