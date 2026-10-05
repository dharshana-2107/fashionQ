<<<<<<< HEAD
# Semantic Fashion Recommendation System

Natural-language, multilingual product search over the Amazon Reviews 2023
(McAuley Lab) **Amazon Fashion** dataset.

| Phase | Goal | Status |
|---|---|---|
| 0. Setup | Infrastructure running, keys and models verified | **this zip** |
| 1. Data | Clean catalog + review aggregates in Postgres | next |
| 2. Enrichment | Haiku extracts attributes + review summaries | |
| 3. Indexing | BGE-M3 dense + sparse vectors in Qdrant | |
| 4. Search | `/search` with parsing, hybrid retrieval, rerank, outfits | |
| 5. Evolving catalog | Catalog service → Redis stream → indexer worker | |
| 6. Evaluation | Recall@50, nDCG@10, latency baseline | |

---

## Phase 0: Setup

### 1. Install these first
- **Docker Desktop** (includes Docker Compose): https://www.docker.com/products/docker-desktop
- **Python 3.11 or 3.12**: https://www.python.org/downloads
- **Git** and an editor (VS Code recommended)
- An **Anthropic API key** with credits: https://console.anthropic.com
- Optional: a **Hugging Face token** (avoids download rate limits): https://huggingface.co/settings/tokens

Hardware: 16 GB RAM, ~20 GB free disk. GPU optional.

### 2. Create a virtual environment (run from the project folder)
```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

### 3. Install PyTorch for your machine, THEN the rest
PyTorch must match your hardware, so install it separately first:
```bash
# CPU only (Windows / Linux without NVIDIA GPU) - smallest download
pip install torch --index-url https://download.pytorch.org/whl/cpu

# macOS (Apple Silicon uses the GPU via "mps" automatically)
pip install torch

# NVIDIA GPU: use the command from https://pytorch.org/get-started/locally/
```
Then:
```bash
pip install -r requirements.txt
```

### 4. Configure secrets
```bash
cp .env.example .env        # Windows: copy .env.example .env
```
Open `.env` and set `ANTHROPIC_API_KEY` (and `HF_TOKEN` if you have one).
Never commit `.env` (it's in `.gitignore`).

### 5. Start the infrastructure
```bash
docker compose up -d
docker compose ps           # postgres and redis should show "healthy"
```
Qdrant's dashboard: http://localhost:6333/dashboard

### 6. Verify everything
```bash
python -m scripts.check_setup              # makes one tiny Haiku call (fractions of a cent)
python -m scripts.check_setup --skip-llm   # no API call
```

### 7. Download and test the local models (~4-5 GB, one time)
```bash
python -m scripts.download_models
```
You should see the English and Tamil "linen shirt" sentences score as more
similar than the winter coat, and the reranker score swim trunks above the coat
for "outfit for the beach". That's semantic + multilingual search working.

**Phase 0 is done when `check_setup` shows all PASS and the model test runs.**

---

## Useful Docker commands
```bash
docker compose logs -f qdrant      # follow a service's logs
docker compose stop                # stop (data kept)
docker compose down                # remove containers (data kept in volumes)
docker compose down -v             # remove containers AND all data (full reset)
```

## Troubleshooting
- **Port already in use (e.g. 5432)**: you have a local Postgres/Redis running.
  Stop it, or change `POSTGRES_PORT` / `REDIS_PORT` in `.env`, then
  `docker compose up -d` again.
- **"Cannot connect to the Docker daemon"**: open Docker Desktop and wait until it's running.
- **Postgres auth failed after changing the password in .env**: the old password is
  stored in the volume. Run `docker compose down -v` then `up -d` (deletes data).
- **Windows: "running scripts is disabled"** when activating the venv:
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
- **FlagEmbedding install / import errors**: usually a `transformers` version clash.
  Try `pip install -U FlagEmbedding`, or a fresh venv installing torch first.
- **Model test is slow**: normal on CPU (first load reads ~2 GB from disk).
- **`ModuleNotFoundError: common`**: run scripts from the project root with
  `python -m scripts.<name>`, not `python scripts/<name>.py`.

## Project layout (folders fill in over later phases)
```
common/      shared config and helpers used by all services
services/    catalog/, search/, indexer/  (phases 4-5)
scripts/     one-off jobs: setup checks, data loading, bulk indexing
eval/        evaluation (phase 6)
demo/        Streamlit UI (phase 4)
data/        downloaded dataset files (git-ignored)
```
=======
# fashionQ
>>>>>>> 43291e636a6e11c772926c2dbcad979238b87c9b
