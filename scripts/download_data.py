"""Phase 1, step 1: download Amazon Reviews 2023 files for one category.

    python -m scripts.download_data                       # Amazon_Fashion
    python -m scripts.download_data --category Clothing_Shoes_and_Jewelry   # much bigger

Files land in data/raw/... and are skipped if already downloaded.
"""
import argparse
import os
from pathlib import Path

from common.config import settings

if settings.hf_token:
    os.environ.setdefault("HF_TOKEN", settings.hf_token)

from huggingface_hub import hf_hub_download, list_repo_files  # noqa: E402

REPO_ID = "McAuley-Lab/Amazon-Reviews-2023"


def remote_paths(category: str) -> dict[str, str]:
    return {
        "meta": f"raw/meta_categories/meta_{category}.jsonl",
        "reviews": f"raw/review_categories/{category}.jsonl",
    }


def local_paths(category: str = "Amazon_Fashion") -> dict[str, Path]:
    """Where the files end up locally (used by load_catalog.py)."""
    return {k: Path(settings.data_dir) / v for k, v in remote_paths(category).items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="Amazon_Fashion")
    args = parser.parse_args()

    for kind, remote in remote_paths(args.category).items():
        target = local_paths(args.category)[kind]
        if target.exists():
            print(f"[skip] {kind}: already at {target} ({target.stat().st_size / 1e9:.2f} GB)")
            continue
        print(f"[download] {kind}: {remote} (large file, please wait)")
        try:
            hf_hub_download(repo_id=REPO_ID, repo_type="dataset",
                            filename=remote, local_dir=settings.data_dir)
        except Exception as e:  # noqa: BLE001
            print(f"\nDownload failed: {type(e).__name__}: {e}")
            print("Files in the repo matching this category:")
            for f in list_repo_files(REPO_ID, repo_type="dataset"):
                if args.category in f:
                    print("  ", f)
            raise SystemExit(1)
        print(f"  saved to {target} ({target.stat().st_size / 1e9:.2f} GB)")

    print("\nDone. Next: python -m scripts.load_catalog")


if __name__ == "__main__":
    main()
