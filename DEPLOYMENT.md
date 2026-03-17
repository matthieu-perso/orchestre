# Orchestre — Production Deployment Guide

## Prerequisites

- Docker & Docker Compose
- Domain name (for HTTPS and webhooks)
- Firebase project
- (Optional) Shopify app, Amazon Seller Central app for commerce features

---

## 1. Prepare configuration

### 1.1 Copy and fill environment variables

```bash
cp .env.sample .env
```

Edit `.env` and set at minimum:

```env
# Required — generate with: openssl rand -hex 32
SESSION_KEY=<your-32-char-minimum-secret>

# Production security
PRODUCTION=true
CORS_ORIGINS=https://your-frontend.com
STRICT_WEBHOOK_VERIFICATION=true

# Database (Docker Compose overrides host to postgres/redis)
POSTGRES_USER=orchestre
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=orchestre
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

REDIS_URL=redis://localhost:6379

# Firebase
FIREBASE_STORAGE_BUCKET=your-project.appspot.com

# Webhooks (your public API URL)
WEBHOOK_BASE_URL=https://api.your-domain.com

# LLM (required for AI features)
OPENAI_API_KEY=sk-...
```

For Shopify/Amazon, add `SHOPIFY_*`, `AMAZON_*`, etc. See `.env.sample`.

### 1.2 Firebase files

Place in the project root:

- **firebase-serviceaccount.json** — Firebase Console → Project Settings → Service Accounts → Generate new private key
- **firebase.json** — Firebase Console → Project Settings → General → Your apps → Config (or create with `apiKey`, `authDomain`, etc.)

### 1.3 Optional: Gmail OAuth app credentials

`oauth2-credentials.json` = your **Gmail app** credentials (client_id, client_secret). Not user tokens — those are stored in Firestore when users connect their accounts.

For Gmail integration, add `oauth2-credentials.json` from Google Cloud Console (OAuth 2.0 Client ID).

If you don't use Gmail, create a placeholder so the volume mount works:
```bash
echo '{}' > oauth2-credentials.json
```

---

## 2. Deploy with Docker Compose

### 2.1 Build and run

```bash
docker compose up -d --build
```

This starts:

- **orchestre** — API on port 8000
- **orchestre-worker** — ARQ background jobs
- **postgres** — Database
- **redis** — Queue/cache

### 2.2 Verify

```bash
# Liveness
curl http://localhost:8000/probe

# Readiness (Postgres + Redis)
curl http://localhost:8000/health
```

### 2.3 Logs

```bash
docker compose logs -f orchestre
```

---

## 3. HTTPS with a reverse proxy

Run Orchestre behind Nginx, Traefik, or Caddy.

### Example: Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name api.your-domain.com;

    ssl_certificate /etc/letsencrypt/live/api.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### Example: Caddy (auto HTTPS)

```caddy
api.your-domain.com {
    reverse_proxy localhost:8000
}
```

---

## 4. GCP Cloud Run (recommended if already on GCP)

### 4.1 One-time setup

1. **Enable APIs**
   ```bash
   gcloud services enable run.googleapis.com artifactregistry.googleapis.com sqladmin.googleapis.com redis.googleapis.com secretmanager.googleapis.com
   ```

2. **Create Artifact Registry**
   ```bash
   gcloud artifacts repositories create orchestre --repository-format=docker --location=us-central1
   ```

3. **Create Cloud SQL (Postgres)**
   ```bash
   gcloud sql instances create orchestre-db --database-version=POSTGRES_16 --tier=db-f1-micro --region=us-central1
   gcloud sql databases create orchestre --instance=orchestre-db
   gcloud sql users set-password postgres --instance=orchestre-db --password=YOUR_PASSWORD
   ```

4. **Create Memorystore (Redis)**
   ```bash
   gcloud redis instances create orchestre-redis --size=1 --region=us-central1
   ```

5. **Store secrets in Secret Manager**
   ```bash
   gcloud secrets create firebase-serviceaccount --data-file=firebase-serviceaccount.json
   gcloud secrets create firebase-config --data-file=firebase.json
   ```

6. **Create a service account for GitHub Actions**
   ```bash
   gcloud iam service-accounts create github-actions --display-name="GitHub Actions"
   gcloud projects add-iam-policy-binding PROJECT_ID --member="serviceAccount:github-actions@PROJECT_ID.iam.gserviceaccount.com" --role="roles/run.admin"
   gcloud projects add-iam-policy-binding PROJECT_ID --member="serviceAccount:github-actions@PROJECT_ID.iam.gserviceaccount.com" --role="roles/artifactregistry.writer"
   gcloud iam service-accounts keys create key.json --iam-account=github-actions@PROJECT_ID.iam.gserviceaccount.com
   ```
   Add the contents of `key.json` as GitHub secret `GCP_SA_KEY`.

### 4.2 GitHub secrets

| Secret | Description |
|--------|-------------|
| `GCP_PROJECT_ID` | Your GCP project ID |
| `GCP_REGION` | e.g. `us-central1` |
| `GCP_SA_KEY` | Full JSON content of the service account key |

### 4.3 Deploy manually (first time)

```bash
# Build and push
docker build --target production -t us-central1-docker.pkg.dev/PROJECT_ID/orchestre/api:latest .
docker push us-central1-docker.pkg.dev/PROJECT_ID/orchestre/api:latest

# Deploy API (set env vars via --set-env-vars or Cloud Console)
gcloud run deploy orchestre-api \
  --image us-central1-docker.pkg.dev/PROJECT_ID/orchestre/api:latest \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "SESSION_KEY=xxx,POSTGRES_HOST=/cloudsql/PROJECT:REGION:INSTANCE,REDIS_URL=redis://REDIS_IP:6379,..."

# Deploy worker
gcloud run deploy orchestre-worker \
  --image us-central1-docker.pkg.dev/PROJECT_ID/orchestre/api:latest \
  --region us-central1 \
  --platform managed \
  --no-allow-unauthenticated \
  --command python --args worker.py
```

For Cloud SQL from Cloud Run, add `--add-cloudsql-instances PROJECT:REGION:INSTANCE` and set `POSTGRES_HOST=/cloudsql/PROJECT:REGION:INSTANCE`. For Redis (Memorystore), create a VPC connector and add `--vpc-connector=NAME`.

### 4.4 Automatic deploy via GitHub Actions

Push to `main` triggers the deploy workflow. In GitHub repo Settings → Secrets and variables → Actions, set:

**Required:** `GCP_PROJECT_ID`, `GCP_SA_KEY`

**Optional (env vars for Cloud Run):** `SESSION_KEY`, `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `REDIS_URL`, `CORS_ORIGINS`, `WEBHOOK_BASE_URL`, `OPENAI_API_KEY`, `FIREBASE_STORAGE_BUCKET`, `STRICT_WEBHOOK_VERIFICATION`

---

## 5. Other cloud options

### Railway / Render / Fly.io

1. Set environment variables in the dashboard.
2. Set build: `Dockerfile` with target `production`.
3. Set `POSTGRES_HOST` and `REDIS_URL` to your managed DB/Redis URLs.
4. Mount or provide `firebase-serviceaccount.json` and `firebase.json` (e.g. via secrets or env vars).

### Kubernetes

1. Build and push the image: `docker build -t your-registry/orchestre:latest --target production .`
2. Deploy Postgres and Redis (or use managed services).
3. Create a Deployment for orchestre and worker.
4. Use ConfigMaps/Secrets for env and Firebase files.
5. Expose the API via Ingress with TLS.

---

## 6. Health checks

| Endpoint   | Purpose                          |
|-----------|-----------------------------------|
| `GET /probe`  | Liveness (process running)       |
| `GET /health` | Readiness (Postgres + Redis OK)  |

Use `/health` for load balancer or orchestrator readiness probes.

---

## 7. Checklist before go-live

- [ ] `SESSION_KEY` is 32+ chars, generated with `openssl rand -hex 32`
- [ ] `PRODUCTION=true`, `CORS_ORIGINS` set, `STRICT_WEBHOOK_VERIFICATION=true`
- [ ] `firebase-serviceaccount.json` and `firebase.json` in place
- [ ] Postgres password changed from default
- [ ] HTTPS enabled (reverse proxy)
- [ ] `WEBHOOK_BASE_URL` points to your public API URL
- [ ] Shopify/Amazon webhook secrets set if using those integrations
