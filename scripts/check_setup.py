"""Phase 0 health check: verifies every piece of the setup is reachable.

Run from the project root:
    python -m scripts.check_setup            # full check (makes one tiny Haiku call)
    python -m scripts.check_setup --skip-llm # skip the paid API call
"""
import argparse
import shutil
import sys
import time

from common.config import settings

results: list[tuple[str, bool, str]] = []


def check(name):
    """Decorator: run a check, record pass/fail without crashing the script."""
    def wrap(fn):
        def run():
            start = time.perf_counter()
            try:
                detail = fn() or ""
                ok = True
            except Exception as e:  # noqa: BLE001 - we want every failure reported
                detail, ok = f"{type(e).__name__}: {e}", False
            ms = (time.perf_counter() - start) * 1000
            results.append((name, ok, f"{detail} ({ms:.0f} ms)"))
        return run
    return wrap


@check("Python version")
def check_python():
    v = sys.version_info
    if not (v.major == 3 and 11 <= v.minor <= 12):
        raise RuntimeError(f"found {v.major}.{v.minor}; use 3.11 or 3.12")
    return f"{v.major}.{v.minor}.{v.micro}"


@check("Disk space")
def check_disk():
    free_gb = shutil.disk_usage(".").free / 1e9
    if free_gb < 10:
        raise RuntimeError(f"only {free_gb:.0f} GB free; need ~20 GB for data + models")
    if free_gb < 20:
        return f"{free_gb:.0f} GB free (warning: ~20 GB recommended)"
    return f"{free_gb:.0f} GB free"


@check("Postgres")
def check_postgres():
    import psycopg
    with psycopg.connect(settings.postgres_dsn, connect_timeout=5) as conn:
        version = conn.execute("SELECT version()").fetchone()[0]
    return version.split(",")[0]


@check("Redis (+ stream write/read)")
def check_redis():
    import redis
    r = redis.Redis.from_url(settings.redis_url, socket_timeout=5)
    r.ping()
    # Prove streams work: write one event, read it back, clean up.
    msg_id = r.xadd("setup_check", {"hello": "world"})
    r.xrange("setup_check", min=msg_id, max=msg_id)
    r.delete("setup_check")
    return f"redis {r.info()['redis_version']}"


@check("Qdrant")
def check_qdrant():
    from qdrant_client import QdrantClient
    client = QdrantClient(url=settings.qdrant_url, timeout=5)
    n = len(client.get_collections().collections)
    return f"{n} collections; dashboard at {settings.qdrant_url}/dashboard"


@check("LLM API")
def check_llm():
    from openai import OpenAI
    if not settings.llm_api_key or "your-" in settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY not set in .env")
    if not settings.llm_model or "paste-" in settings.llm_model:
        raise RuntimeError("LLM_MODEL not set in .env")
    client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
    resp = client.chat.completions.create(
        model=settings.llm_model,
        max_tokens=1000,  # room for the model's internal thinking + the answer
        messages=[{"role": "user", "content": "Reply with exactly: OK"}],
    )
    reply = (resp.choices[0].message.content or "").strip()
    if not reply:
        raise RuntimeError(
            f"empty reply (finish_reason={resp.choices[0].finish_reason}); "
            "try a larger max_tokens"
        )
    return f"{settings.llm_model} replied '{reply}'"


@check("PyTorch device")
def check_torch():
    import torch
    from common.device import get_device
    return f"torch {torch.__version__}, device = {get_device()}"


@check("FlagEmbedding import")
def check_flag():
    import FlagEmbedding  # noqa: F401
    return "ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-llm", action="store_true", help="skip the Haiku API call")
    args = parser.parse_args()

    checks = [check_python, check_disk, check_postgres, check_redis, check_qdrant,
              check_torch, check_flag]
    if not args.skip_llm:
        checks.append(check_llm)
    for c in checks:
        c()

    width = max(len(n) for n, _, _ in results)
    print("\nPhase 0 setup check\n" + "-" * 60)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = [n for n, ok, _ in results if not ok]
    print("-" * 60)
    if failed:
        print(f"{len(failed)} check(s) failed: {', '.join(failed)}. See README troubleshooting.")
        sys.exit(1)
    print("All good. Next: python -m scripts.download_models")


if __name__ == "__main__":
    main()
