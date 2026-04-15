# Deploy to AWS EC2 with GitHub Actions (Simple CI/CD)

This is a simple SSH-based deployment pipeline for this project.

- CI: runs tests on pull requests and non-main pushes.
- CD: on `main` (or manual trigger), uploads source to EC2 and runs `docker compose up -d --build`.

## 1) One-time EC2 setup

Assumptions:

- Ubuntu EC2 instance
- Ports open in security group:
  - `22` (SSH)
  - `8000` (FastAPI)
  - `8501` (Streamlit)

Run on EC2:

```bash
# Install Docker via the official script (works on Ubuntu 22.04 and 24.04)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version

# Create app directory (optional — the deploy workflow falls back to ~/customer_support_agent)
sudo mkdir -p /opt/customer_support_agent
sudo chown -R $USER:$USER /opt/customer_support_agent
```

> The deploy workflow will install Docker automatically via `get.docker.com` if it is not present — so this step is optional for fresh instances.

Create runtime env file once:

```bash
cat > /opt/customer_support_agent/.env <<'EOF_ENV'
GROQ_API_KEY=your_real_key
GROQ_MODEL=openai/gpt-oss-20b
API_BASE_URL=http://localhost:8000
EOF_ENV
```

**Recommended alternative:** set GitHub secret `EC2_ENV_FILE` to the full contents of your `.env` file. The deploy workflow will inject it automatically on every deploy, so you never have to touch the server again. No repository variable is needed — the workflow writes `.env` whenever the secret is non-empty.

## 2) GitHub Actions workflows added

- `/.github/workflows/ci.yml`
  - triggers: `pull_request`, push to non-main branches
  - steps: checkout -> setup python+uv -> `uv sync --dev` -> `uv run pytest -q`

- `/.github/workflows/deploy-ec2.yml`
  - triggers: `push` to `main`, manual `workflow_dispatch`
  - concurrency group: `deploy-ec2` (cancels in-progress deploy if a newer push arrives)
  - jobs:
    1. **test** — same as CI workflow
    2. **deploy** — needs test to pass
  - deploy steps:
    - inject `.env` from `EC2_ENV_FILE` secret (if non-empty)
    - configure SSH key
    - package release tar (excludes `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `artifacts`)
    - upload tar to EC2 via `scp` (3 retries with TCP connectivity pre-check)
    - SSH to EC2 and run remote script:
      - extract to app dir (falls back to `~/customer_support_agent` if `/opt/` not writable)
      - install Docker via `get.docker.com` if not present
      - auto-detect `docker compose` vs `docker-compose`
      - prune old images/containers to free disk space
      - `docker compose up -d --build --force-recreate --remove-orphans`
      - poll `http://127.0.0.1:8000/health` for up to 60 s
    - SSH step retries up to 3 times on transient failures

## 3) Required GitHub secrets

Set in repository settings -> Secrets and variables -> Actions:

- `EC2_HOST` : public IP/DNS of EC2
- `EC2_USER` : SSH user (usually `ubuntu`)
- `EC2_SSH_KEY` : private key content for EC2 access

Optional:

- `EC2_PORT` : default `22`
- `EC2_APP_DIR` : default `/opt/customer_support_agent`
- `EC2_ENV_FILE` : full `.env` file content (multi-line). Set this and the workflow injects it automatically — no repository variable needed.

## 4) Deployment flow

1. Merge/push changes to `main`.
2. GitHub Actions runs tests.
3. If tests pass, deploy job uploads project and restarts containers on EC2.
4. Verify:

```bash
curl http://<EC2_PUBLIC_IP>:8000/health
# expected: {"status":"ok"}
```

Open UI:

- FastAPI docs: `http://<EC2_PUBLIC_IP>:8000/docs`
- Streamlit: `http://<EC2_PUBLIC_IP>:8501`

## 5) Rollback

The deploy workflow does not keep previous releases on the server. To roll back:

```bash
# On your local machine — push the previous commit to main
git revert HEAD --no-edit
git push origin main
# GitHub Actions will re-deploy the reverted code automatically
```

Or manually on EC2:

```bash
cd ~/customer_support_agent  # or /opt/customer_support_agent
docker compose down
# restore previous source, then:
docker compose up -d --build
```

## 6) Notes

- This is intentionally simple (SSH + docker compose, no registry).
- For stronger production posture later:
  - add Nginx + TLS (Let's Encrypt / ACM)
  - move secrets to AWS SSM / Secrets Manager
  - push images to GHCR and pull on EC2 instead of uploading source
  - add blue/green or canary deploy with a load balancer
