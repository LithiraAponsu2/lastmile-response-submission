"""Bounded private uploads; PDF extraction runs outside the request process."""
import base64
import hashlib
import json
import subprocess
import sys

from .team_accounts import require
from .workspace import WorkspaceError


def attachment(data):
    require(isinstance(data, dict) and set(data) == {"name", "base64"}, "Upload needs a file name and base64 bytes.", 422)
    name, encoded = data["name"], data["base64"]
    require(isinstance(name, str) and 1 <= len(name) <= 120 and not any(c in name for c in '/\\\r\n\x00'), "Use a simple file name.", 422)
    require(isinstance(encoded, str) and len(encoded) <= 2800000, "Maximum attachment size is 2 MB.", 413)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise WorkspaceError("Invalid file encoding.", 422) from None
    require(0 < len(raw) <= 2000000, "Maximum attachment size is 2 MB.", 413)
    pages = []
    if name.casefold().endswith(".pdf"):
        require(raw.startswith(b"%PDF-"), "The file is not a PDF.", 422)
        try:
            result = subprocess.run([sys.executable, "-m", "lastmile.team_documents"], input=raw, capture_output=True, timeout=12, check=True)
            pages = json.loads(result.stdout)
            text = "\n\n".join(f"[Page {p['page']}]\n{p['text']}" for p in pages if p["text"].strip())
        except (subprocess.SubprocessError, ValueError):
            raise WorkspaceError("PDF extraction failed or exceeded its limit. Upload a smaller text PDF or a labelled transcript.", 422) from None
        mime = "application/pdf"
    else:
        require(name.casefold().endswith(".txt"), "Upload a text PDF or UTF-8 .txt file.", 422)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise WorkspaceError("Text attachments must use UTF-8.", 422) from None
        mime = "text/plain; charset=utf-8"
    require(len(text) <= 60000, "Extracted document exceeds 60,000 characters. Split it into smaller contributions.", 413)
    return {"name": name, "base64": encoded, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "mime": mime,
            "extraction": "text_extracted" if text.strip() else "needs_transcript", "pages": pages}, text or "[No extractable text. A labelled transcript is required before interpreting this attachment.]"


if __name__ == "__main__":
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read(2000001)), strict=True)
    if reader.is_encrypted or len(reader.pages) > 30:
        raise ValueError("Encrypted or oversized PDF")
    output = []
    total = 0
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        total += len(text)
        if total > 60000:
            raise ValueError("Text limit")
        output.append({"page": number, "text": text})
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False).encode("utf-8"))
