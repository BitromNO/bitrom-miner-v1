# Bitrom Miner — umbrelOS app package

Store-ready Umbrel package for **Bitrom Miner v1**, the Linux solo CPU Bitcoin
miner. It runs completely in your browser: dashboard, cooling control, and
wallet/pool setup — no SSH or terminal needed. Includes a live home screen
widget (`four-stats`: Hashrate, Accepted, Submitted, Blocks).

## What's inside

| File | Purpose |
|---|---|
| `umbrel-app.yml` | umbrelOS app metadata + widget registration |
| `docker-compose.yml` | services: `app_proxy` (web UI) + `server` (miner + API) |
| `Dockerfile` | Multi-stage: builds `tpruvot/cpuminer-multi`, slim runtime |
| `README.md` | This file |

The app container runs the same code as the desktop app but in `--headless
--web` mode: the TUI is replaced by the web dashboard
(`GET /api/status`, `POST /api/cooling`, `POST /api/settings`,
`GET /api/widget`).

Data lives in `${APP_DATA_DIR}/data` (config + cache) and is backed up by
umbrelOS. Images must be `linux/amd64` + `linux/arm64`.

## Before store submission: build, publish, and pin the image

The store requires a **prebuilt multi-arch image pinned by digest** and does
not allow a `build:` key in `docker-compose.yml`. Do this from the **repo
root** (build context includes `bitrom.py`, `bitrom/`, `README.md`):

```sh
# 1. Authenticate to the container registry (GitHub Container Registry)
echo "$CR_PAT" | docker login ghcr.io -u BitromNO --password-stdin

# 2. Build + push both architectures
docker buildx build --platform linux/amd64,linux/arm64 \
  -f umbrel/bitrom-miner/Dockerfile \
  -t ghcr.io/bitromno/bitrom-miner:1.0.0 \
  --push .

# 3. Read the multi-arch manifest digest
docker buildx imagetools inspect ghcr.io/bitromno/bitrom-miner:1.0.0
```

Copy the `Digest: sha256:...` line into `docker-compose.yml`:

```yaml
image: ghcr.io/bitromno/bitrom-miner:1.0.0@sha256:<REPLACE_ME>
```

> Enable GHCR packages for the repo (Settings → Packages → enable write
> `bitromno/bitrom-miner`), or push to Docker Hub instead and adjust `image:`.

## Local test without Umbrel

```sh
docker compose -f umbrel/bitrom-miner/docker-compose.yml up -d
open http://localhost:8080        # dashboard
curl http://localhost:8080/api/widget   # widget payload
docker compose -f umbrel/bitrom-miner/docker-compose.yml down
```

(For a local test run, temporarily add `- <<services.server.ports: - "8080:8080"`)

## Submit to the store

1. Open a PR against `getumbrel/umbrel-apps` with the contents of this folder
   + the `Dockerfile`.
2. Run `npx @getumbrel/umbrel-package-app validate` after setting the real
   `image:` digest.
3. Fix the `submission:` URL in `umbrel-app.yml` to the PR link.
4. Check with the store gate checklist:
   - [ ] Opens to a web UI (`app:` service → `app_proxy`)
   - [ ] No SSH/CLI/env-var setup required by end users (all in the web UI)
   - [ ] Data persists via `${APP_DATA_DIR}/data`
   - [ ] Image prebuilt multi-arch, pinned `repo:tag@sha256`, no `build:`
   - [ ] `restart: on-failure`, `init: true`, runs as `1000:1000`
   - [ ] Widget endpoint reachable: `server:8080/api/widget`
   - [ ] Blocking mining backends, Tor-only pools, etc. avoided — public-pool.io
         is a clearnet-solo pool reachable from the app's default network.