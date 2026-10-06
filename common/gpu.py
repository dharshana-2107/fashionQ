"""One lock for all GPU work in a process.

PyTorch's Apple GPU backend (MPS) is not thread-safe: if two threads run models
at the same moment (e.g. the embedder for one search and the reranker for
another), macOS aborts the whole process with
"failed assertion _status < MTLCommandBufferStatusCommitted".
Every model call takes this lock, so GPU work in a process runs one at a time.
Separate processes (search API vs indexer worker) are fine.
"""
import threading

GPU_LOCK = threading.RLock()
