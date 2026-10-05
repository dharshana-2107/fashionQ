"""Download BGE-M3 and the reranker once, then run a quick sanity test.

    python -m scripts.download_models
    python -m scripts.download_models --skip-test   # download only

Models are cached in HF_HOME (default ./models_cache), ~4-5 GB total.
"""
import argparse
import os
import time

from common.config import settings

# Must be set BEFORE importing huggingface libraries so the cache location applies.
os.environ.setdefault("HF_HOME", settings.hf_home)
if settings.hf_token:
    os.environ.setdefault("HF_TOKEN", settings.hf_token)

from huggingface_hub import snapshot_download  # noqa: E402


def download(repo_id: str):
    print(f"Downloading {repo_id} ...")
    path = snapshot_download(repo_id=repo_id)
    print(f"  cached at {path}")


def test_models():
    from FlagEmbedding import BGEM3FlagModel, FlagReranker
    from common.device import get_device, use_fp16

    device = get_device()
    fp16 = use_fp16(device)
    print(f"\nTesting on device={device} (fp16={fp16})")

    t = time.perf_counter()
    embedder = BGEM3FlagModel(settings.embed_model, use_fp16=fp16, devices=device)
    texts = [
        "breathable linen shirt for the beach",       # English
        "கடற்கரைக்கு ஏற்ற லினன் சட்டை",                  # Tamil
        "winter wool coat for office",                # unrelated
    ]
    out = embedder.encode(texts, return_dense=True, return_sparse=True)
    dense = out["dense_vecs"]
    print(f"BGE-M3 loaded + encoded in {time.perf_counter() - t:.1f}s; "
          f"dense dim = {dense.shape[1]}, sparse terms in text 1 = {len(out['lexical_weights'][0])}")

    # Dense vectors are normalized, so dot product = cosine similarity.
    print(f"  similarity EN vs Tamil (same meaning): {float(dense[0] @ dense[1]):.3f}")
    print(f"  similarity EN vs winter coat:          {float(dense[0] @ dense[2]):.3f}")

    t = time.perf_counter()
    reranker = FlagReranker(settings.rerank_model, use_fp16=fp16, devices=device)
    scores = reranker.compute_score(
        [["outfit for the beach", "men's quick-dry swim trunks"],
         ["outfit for the beach", "wool winter coat"]],
        normalize=True,
    )
    print(f"Reranker loaded + scored in {time.perf_counter() - t:.1f}s; "
          f"swim trunks = {scores[0]:.3f}, wool coat = {scores[1]:.3f}")
    print("\nIf the matching pairs score higher than the mismatched ones, models work.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-test", action="store_true")
    args = parser.parse_args()
    download(settings.embed_model)
    download(settings.rerank_model)
    if not args.skip_test:
        test_models()


if __name__ == "__main__":
    main()
