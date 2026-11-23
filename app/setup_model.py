from pathlib import Path
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent
snapshot_download(
    repo_id="iapp/OpenThai2.0-OCR-Text",
    local_dir=ROOT / "model",
    cache_dir=ROOT / ".download-cache",
    allow_patterns=["*.onnx", "*.json", "*.txt", "*.yml", "runtime/*", "LICENSE*", "NOTICE*", "README.md", "SHA256SUMS"],
)
print("Model downloaded. You can now start the application offline.")
