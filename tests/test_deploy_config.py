"""The container must trust Cloud Run's proxy headers, or every account action fails its origin check in the cloud."""
from pathlib import Path


def test_dockerfile_trusts_cloud_run_proxy_headers():
    cmd = next(line for line in (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8").splitlines() if line.startswith("CMD"))
    assert "--proxy-headers" in cmd and "--forwarded-allow-ips=*" in cmd
