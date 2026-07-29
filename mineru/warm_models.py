"""Exercise the local MinerU pipeline before an offline or intranet rollout.

This module is only invoked through the explicit Compose warmup profile.
It creates a temporary, controlled probe PDF, runs the exact same local CLI
used for researcher uploads, and removes the probe when finished.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app import run_mineru


def write_probe_pdf(destination: Path) -> None:
    """Write a tiny valid text PDF using only the Python standard library."""
    stream = b"BT\n/F1 18 Tf\n72 720 Td\n(Longyun Agent MinerU warmup) Tj\nET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    ]

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    chunks = [header]
    offsets = [0]
    position = len(header)
    for object_number, payload in enumerate(objects, start=1):
        encoded = f"{object_number} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"
        offsets.append(position)
        chunks.append(encoded)
        position += len(encoded)

    xref_position = position
    xref = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    trailer = (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_position).encode("ascii")
        + b"\n%%EOF\n"
    )
    destination.write_bytes(b"".join(chunks + xref + [trailer]))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mineru-warmup-") as temporary_directory:
        probe_path = Path(temporary_directory) / "mineru-warmup.pdf"
        write_probe_pdf(probe_path)
        markdown = run_mineru(probe_path)
        if not markdown.strip():
            raise RuntimeError("MinerU warmup did not produce readable Markdown.")
    print("MinerU local model files and parsing pipeline are ready.")


if __name__ == "__main__":
    main()
