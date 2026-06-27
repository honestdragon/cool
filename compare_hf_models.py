#!/usr/bin/env python3
"""
Compare two Hugging Face models with identical structure: compute per-parameter
relative deltas (%), bucket them, and plot bar chart and pie chart.

- Models are downloaded to download_models/ (next to this script) with multiple
  workers; if a model already exists there, it is reused.
- Delta computation runs on GPU when available (CUDA).

Uses a strict shard-by-shard, one-tensor-at-a-time approach: only the header of
each shard is read; then for each parameter we seek and read only that tensor's
bytes. This keeps memory use low (no full-shard mmap, no loading both models).

Usage:
    python compare_hf_models.py MODEL_A MODEL_B [--out-dir DIR] [--bins N] [--max-delta %]
    python compare_hf_models.py org/model-a org/model-b --out-dir ./comparison
    
    python3 compare_hf_models.py \
        origin/teutonic-5dawwwmr-7971731779-cp1 \
        origin/teutonic-q3-10b-5ek5koe5-97319140083-rn-l4 \
        --out-dir ./results/miner38-vs-k7p4m2
    

Requires: huggingface_hub, torch, numpy, matplotlib
"""

from __future__ import annotations

import argparse
import json
import logging
import struct
import sys
from collections import defaultdict
from pathlib import Path

# Log every computing step to stdout so user sees progress (e.g. during long delta computation)


class _FlushStreamHandler(logging.StreamHandler):
    """Flush after each log so progress is visible immediately during long runs."""

    def emit(self, record):
        super().emit(record)
        self.flush()


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
if not log.handlers:
    h = _FlushStreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(h)

from huggingface_hub import snapshot_download

# Dtype name (safetensors) -> size in bytes
DTYPE_BYTES = {
    "F16": 2, "BF16": 2, "F32": 4, "F64": 8,
    "I8": 1, "I16": 2, "I32": 4, "I64": 8,
    "U8": 1, "U16": 2, "U32": 4, "U64": 8,
    "BOOL": 1,
}

# Safetensors dtype string -> numpy dtype for reading raw buffer (little-endian)
DTYPE_TO_NUMPY = {
    "F16": "<f2", "BF16": "<f2", "F32": "<f4", "F64": "<f8",
    "I8": "<i1", "I16": "<i2", "I32": "<i4", "I64": "<i8",
    "U8": "<u1", "U16": "<u2", "U32": "<u4", "U64": "<u8",
    "BOOL": "|b1",
}

# Epsilon to avoid division by zero when computing relative delta
DELTA_EPS = 1e-8


def parse_args():
    p = argparse.ArgumentParser(
        description="Compare two Hugging Face models: parameter deltas (%%), histograms, bar and pie charts."
    )
    p.add_argument("model_a", type=str, help="First model: Hugging Face repo id or local path")
    p.add_argument("model_b", type=str, help="Second model: Hugging Face repo id or local path")
    p.add_argument(
        "--out-dir",
        type=str,
        default="./compare_output",
        help="Directory for output plots and summary (default: ./compare_output)",
    )
    p.add_argument(
        "--bins",
        type=int,
        default=31,
        help="Number of histogram bins for delta %% (symmetric around 0). Default 31 (e.g. -5%% to +5%% in 0.33%% steps).",
    )
    p.add_argument(
        "--max-delta",
        type=float,
        default=10.0,
        help="Max delta %% for bin range; values outside [-max-delta, +max-delta] go into edge bins (default: 10).",
    )
    p.add_argument(
        "--hf-token",
        type=str,
        default=None,
        help="Hugging Face token for private repos",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Download workers when using repo ids (default: 4).",
    )
    p.add_argument(
        "--no-pie",
        action="store_true",
        help="Skip pie chart (only bar chart).",
    )
    p.add_argument(
        "--download-dir",
        type=str,
        default=None,
        help="Directory where models are downloaded and reused (default: download_models next to this script).",
    )
    p.add_argument(
        "--device",
        type=str,
        default=None,
        choices=("cuda", "cpu", "auto"),
        help="Device for computing deltas: cuda, cpu, or auto (default: auto = cuda if available).",
    )
    return p.parse_args()


def _safetensors_header_key_sizes(path: Path) -> dict[str, int]:
    """Read safetensors file header; return dict key -> size in bytes."""
    with open(path, "rb") as f:
        header_len = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(header_len).decode("utf-8"))
    key_sizes = {}
    for name, meta in header.items():
        if name == "__metadata__":
            continue
        shape = meta.get("shape", [])
        dtype = meta.get("dtype", "F32")
        n = 1
        for s in shape:
            n *= s
        key_sizes[name] = n * DTYPE_BYTES.get(dtype, 4)
    return key_sizes


def _read_safetensors_header_with_offsets(path: Path) -> tuple[int, dict[str, dict]]:
    """
    Read only the safetensors header. Returns (data_start_offset, key_metadata).
    data_start_offset = 8 + header_len (first byte of tensor data in file).
    key_metadata[key] = {"dtype": str, "shape": list, "data_offsets": [start, end]}.
    If data_offsets is missing, we compute from key order and tensor sizes.
    """
    with open(path, "rb") as f:
        header_len = struct.unpack("<Q", f.read(8))[0]
        header_bytes = f.read(header_len)
    header = json.loads(header_bytes.decode("utf-8"))
    data_start = 8 + header_len
    key_meta = {}
    running_offset = 0
    for name, meta in header.items():
        if name == "__metadata__":
            continue
        shape = list(meta.get("shape", []))
        dtype = meta.get("dtype", "F32")
        n = 1
        for s in shape:
            n *= s
        num_bytes = n * DTYPE_BYTES.get(dtype, 4)
        offs = list(meta.get("data_offsets", []))
        if len(offs) >= 2 and (offs[1] - offs[0]) == num_bytes:
            key_meta[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offs[0], offs[1]]}
        else:
            key_meta[name] = {
                "dtype": dtype,
                "shape": shape,
                "data_offsets": [running_offset, running_offset + num_bytes],
            }
        running_offset += num_bytes
    return data_start, key_meta


def _load_one_tensor_from_file(
    path: Path,
    dtype_str: str,
    shape: list[int],
    file_offset: int,
    num_bytes: int,
):
    """
    Load a single tensor by seeking and reading only its bytes. Keeps peak memory
    to one tensor only (no full-shard mmap).
    """
    import numpy as np
    import torch

    if dtype_str == "BF16":
        np_dtype = "<u2"
    else:
        np_dtype = DTYPE_TO_NUMPY.get(dtype_str, "<f4")
    with open(path, "rb") as f:
        f.seek(file_offset)
        buf = f.read(num_bytes)
    arr = np.frombuffer(buf, dtype=np.dtype(np_dtype)).reshape(shape).copy()
    if dtype_str == "BF16":
        # Safetensors stores BF16 as raw 16-bit words; reinterpret them before
        # converting to float32 for arithmetic.
        t = torch.from_numpy(arr).view(torch.bfloat16)
    else:
        t = torch.from_numpy(arr)
    return t.float()


def _get_weight_map_and_sizes(model_dir: Path) -> tuple[dict[str, str], dict[str, int] | None]:
    """Return (weight_map, key_to_size). key_to_size may be None."""
    model_dir = Path(model_dir)
    index_path = model_dir / "model.safetensors.index.json"
    if not index_path.exists():
        index_path = model_dir / "pytorch_model.bin.index.json"

    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
        weight_map = index.get("weight_map", {})
        if not weight_map:
            return weight_map, None
        first_file = model_dir / next(iter(weight_map.values()))
        if first_file.suffix != ".safetensors":
            return weight_map, None
        key_to_size = {}
        for shard_file in set(weight_map.values()):
            path = model_dir / shard_file
            for k, size in _safetensors_header_key_sizes(path).items():
                key_to_size[k] = size
        return weight_map, key_to_size

    single = list(model_dir.glob("*.safetensors"))
    if len(single) == 1:
        path = single[0]
        key_sizes = _safetensors_header_key_sizes(path)
        weight_map = {k: path.name for k in key_sizes}
        return weight_map, key_sizes
    return {}, None


def _model_dir_has_weights(path: Path) -> bool:
    """True if path looks like a downloaded model (has index or safetensors)."""
    path = Path(path)
    if not path.is_dir():
        return False
    if (path / "model.safetensors.index.json").exists():
        return True
    if (path / "pytorch_model.bin.index.json").exists():
        return True
    if list(path.glob("*.safetensors")):
        return True
    if list(path.glob("*.bin")):
        return True
    return False


def _resolve_model_path(
    model_id: str,
    token: str | None,
    workers: int,
    download_dir: Path,
) -> Path:
    """
    Return local path to model. If model_id is an existing directory, use it.
    Otherwise use download_dir / sanitized_repo_id: if that path already has
    weights, reuse it; else download from Hugging Face to that path.
    """
    p = Path(model_id)
    if p.is_dir():
        return p.resolve()
    # Sanitize repo id for use as folder name (e.g. org/model-name -> org_model-name)
    safe_name = model_id.replace("/", "_")
    dest = Path(download_dir) / safe_name
    if _model_dir_has_weights(dest):
        print(f"Using existing download: {dest}")
        return dest
    print(f"Downloading {model_id} to {dest} ...")
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=model_id, local_dir=str(dest), token=token, max_workers=workers)
    return dest


def _build_bin_edges(bins: int, max_delta: float):
    """Linear bins from -max_delta to +max_delta (and we clip values into range for counts)."""
    import numpy as np
    return np.linspace(-max_delta, max_delta, bins + 1)


def _build_key_to_tensor_locations(
    model_dir: Path, weight_map: dict[str, str]
) -> dict[str, tuple[Path, str, list[int], int, int]]:
    """
    For each key, return (path, dtype, shape, file_offset, num_bytes) so we can
    load that tensor by seeking and reading only its bytes. No full shard is
    ever loaded.
    """
    key_to_loc: dict[str, tuple[Path, str, list[int], int, int]] = {}
    for shard_file in set(weight_map.values()):
        path = model_dir / shard_file
        data_start, key_meta = _read_safetensors_header_with_offsets(path)
        for k, meta in key_meta.items():
            offs = meta["data_offsets"]
            file_offset = data_start + offs[0]
            num_bytes = offs[1] - offs[0]
            key_to_loc[k] = (
                path,
                meta["dtype"],
                meta["shape"],
                file_offset,
                num_bytes,
            )
    return key_to_loc


def compute_delta_histogram(
    dir_a: Path,
    dir_b: Path,
    bin_edges,
    device=None,
) -> tuple[list[float], list[int], int]:
    """
    Shard-by-shard, one-tensor-at-a-time: for each parameter we open the shard
    file, seek to the tensor's bytes, read only those bytes, then close. Never
    mmap or load a full shard. Peak memory = two tensors (one from A, one from B).
    If device is provided (e.g. cuda), tensors are moved to it for delta computation.
    """
    import gc
    import numpy as np
    import torch

    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    log.info("Loading weight maps for model A: %s", dir_a)
    weight_map_a, _ = _get_weight_map_and_sizes(dir_a)
    log.info("Loading weight maps for model B: %s", dir_b)
    weight_map_b, _ = _get_weight_map_and_sizes(dir_b)
    if not weight_map_a or not weight_map_b:
        raise FileNotFoundError("Could not find safetensors weight maps in one or both model dirs.")
    keys_a = set(weight_map_a.keys())
    keys_b = set(weight_map_b.keys())
    log.info("Model A parameters: %s, Model B parameters: %s", len(keys_a), len(keys_b))
    if keys_a != keys_b:
        raise ValueError(
            f"Parameter sets differ. Only in A: {keys_a - keys_b}. Only in B: {keys_b - keys_a}."
        )

    log.info("Building tensor locations for model A (shard headers)...")
    loc_a = _build_key_to_tensor_locations(dir_a, weight_map_a)
    log.info("Building tensor locations for model B (shard headers)...")
    loc_b = _build_key_to_tensor_locations(dir_b, weight_map_b)
    sorted_keys = sorted(keys_a)
    num_keys = len(sorted_keys)
    log.info("Starting delta computation over %s tensors...", num_keys)

    counts = np.zeros(len(bin_edges) - 1, dtype=np.int64)
    total = 0
    lo, hi = float(bin_edges[0]), float(bin_edges[-1])

    for idx, k in enumerate(sorted_keys):
        log.info("[%s/%s] Load tensor: %s", idx + 1, num_keys, k)
        path_a, dtype_a, shape_a, off_a, nb_a = loc_a[k]
        path_b, dtype_b, shape_b, off_b, nb_b = loc_b[k]
        t_a = _load_one_tensor_from_file(path_a, dtype_a, shape_a, off_a, nb_a)
        t_b = _load_one_tensor_from_file(path_b, dtype_b, shape_b, off_b, nb_b)
        if t_a.shape != t_b.shape:
            raise ValueError(f"Shape mismatch '{k}': {t_a.shape} vs {t_b.shape}")
        numel = t_a.numel()
        log.info("[%s/%s] Compute delta on device (numel=%s)...", idx + 1, num_keys, f"{numel:,}")
        t_a = t_a.to(device)
        t_b = t_b.to(device)
        denom = t_a.abs() + DELTA_EPS
        delta_pct = torch.where(denom > 0, (t_b - t_a) / denom * 100.0, torch.zeros_like(t_a))
        log.info("[%s/%s] Clip and histogram...", idx + 1, num_keys)
        delta_clipped = delta_pct.clamp(lo, hi).cpu().numpy().ravel()
        hist, _ = np.histogram(delta_clipped, bins=bin_edges)
        counts += hist
        total += delta_pct.numel()
        log.info("[%s/%s] Done %s (running total elements: %s)", idx + 1, num_keys, k, f"{total:,}")
        del t_a, t_b, denom, delta_pct, delta_clipped
        gc.collect()
        if device.type == "cuda":
            torch.cuda.empty_cache()

    log.info("Delta computation finished. Building bin centers...")
    bin_centers = [(bin_edges[i] + bin_edges[i + 1]) / 2 for i in range(len(bin_edges) - 1)]
    return bin_centers, counts.tolist(), int(total)


def plot_bar_and_pie(
    bin_centers: list[float],
    counts: list[int],
    total: int,
    out_dir: Path,
    model_a_name: str,
    model_b_name: str,
    no_pie: bool,
) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("matplotlib not installed; skipping bar and pie charts. Install with: pip install matplotlib")
        return

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    labels = [f"{c:.2f}%" for c in bin_centers]
    counts_arr = np.array(counts)

    # Bar chart
    fig, ax = plt.subplots(figsize=(14, 6))
    x = np.arange(len(labels))
    bars = ax.bar(x, counts, color="steelblue", edgecolor="navy", alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_xlabel("Relative delta % (model B − model A) / |model A| × 100")
    ax.set_ylabel("Parameter count")
    ax.set_title(f"Parameter delta distribution: {model_a_name} vs {model_b_name}\n(total {total:,} elements)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    plt.tight_layout()
    bar_path = out_dir / "compare_delta_barchart.png"
    plt.savefig(bar_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved bar chart: {bar_path}")

    if no_pie:
        return

    # Pie chart: use same bins; filter out zero-count slices for clarity if many bins
    nonzero = counts_arr > 0
    if nonzero.sum() == 0:
        print("No nonzero bins; skipping pie chart.")
        return
    pie_centers = [bin_centers[i] for i in range(len(bin_centers)) if counts[i] > 0]
    pie_counts = [counts[i] for i in range(len(counts)) if counts[i] > 0]
    pie_labels = [f"{c:.2f}%\n({n:,})" for c, n in zip(pie_centers, pie_counts)]
    fig2, ax2 = plt.subplots(figsize=(10, 10))
    colors = plt.cm.RdYlGn_r(np.linspace(0.2, 0.8, len(pie_counts)))
    wedges, texts, autotexts = ax2.pie(
        pie_counts,
        labels=pie_labels,
        autopct="%1.1f%%",
        startangle=90,
        colors=colors,
    )
    for t in texts:
        t.set_fontsize(8)
    ax2.set_title(f"Parameter delta distribution (pie): {model_a_name} vs {model_b_name}")
    plt.tight_layout()
    pie_path = out_dir / "compare_delta_piechart.png"
    plt.savefig(pie_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved pie chart: {pie_path}")


def write_summary(out_dir: Path, bin_centers: list[float], counts: list[int], total: int) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "delta_pct_center,count,percent",
    ]
    for c, n in zip(bin_centers, counts):
        pct = (n / total * 100) if total else 0
        lines.append(f"{c:.4f},{n},{pct:.4f}")
    summary_path = out_dir / "compare_delta_summary.csv"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved summary CSV: {summary_path}")


def main() -> None:
    args = parse_args()
    import numpy as np
    import torch

    log.info("=== Compare HF models: start ===")

    # Device for delta computation (GPU if available)
    if args.device == "cpu":
        device = torch.device("cpu")
    elif args.device == "cuda":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        log.info("Using GPU for computation: %s", torch.cuda.get_device_name(0))
    else:
        log.info("Using CPU for computation (no CUDA device found).")

    # Persistent directory for downloaded models (reused across runs)
    if args.download_dir is not None:
        download_dir = Path(args.download_dir).resolve()
    else:
        download_dir = Path(__file__).resolve().parent / "download_models"
    download_dir.mkdir(parents=True, exist_ok=True)
    log.info("Download dir: %s", download_dir)

    log.info("Building bin edges: bins=%s, max_delta=%s%%", args.bins, args.max_delta)
    bin_edges = _build_bin_edges(args.bins, args.max_delta)

    log.info("Resolving model A: %s", args.model_a)
    if Path(args.model_a).is_dir() and Path(args.model_b).is_dir():
        dir_a = Path(args.model_a).resolve()
        dir_b = Path(args.model_b).resolve()
        log.info("Resolving model B: %s", args.model_b)
        log.info("Using local dirs: A=%s, B=%s", dir_a, dir_b)
    else:
        dir_a = _resolve_model_path(args.model_a, args.hf_token, args.workers, download_dir)
        log.info("Resolving model B: %s", args.model_b)
        dir_b = _resolve_model_path(args.model_b, args.hf_token, args.workers, download_dir)
        log.info("Model dirs: A=%s, B=%s", dir_a, dir_b)

    log.info("Computing per-parameter deltas and binning (this may take a while)...")
    bin_centers, counts, total = compute_delta_histogram(dir_a, dir_b, bin_edges, device=device)
    log.info("Total parameter elements: %s", f"{total:,}")

    out_dir = Path(args.out_dir)
    name_a = Path(args.model_a).name if Path(args.model_a).is_dir() else args.model_a
    name_b = Path(args.model_b).name if Path(args.model_b).is_dir() else args.model_b
    log.info("Writing summary CSV to %s...", out_dir)
    write_summary(out_dir, bin_centers, counts, total)
    log.info("Plotting bar and pie charts...")
    plot_bar_and_pie(
        bin_centers, counts, total, out_dir, name_a, name_b, args.no_pie
    )
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
