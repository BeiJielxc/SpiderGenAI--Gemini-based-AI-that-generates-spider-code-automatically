"""
Artifact storage for large tool payloads to keep LLM context compact.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ArtifactRef:
    artifact_id: str
    path: str
    media_type: str
    size_bytes: int
    preview: str

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "path": self.path,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "preview": self.preview,
        }


class ArtifactStore:
    """Simple file-based artifact store."""

    def __init__(self, root_dir: str | Path, max_preview_chars: int = 300):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.max_preview_chars = max_preview_chars

    def _build_path(self, prefix: str, suffix: str) -> tuple[str, Path]:
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        artifact_id = f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"
        filename = f"{artifact_id}{suffix}"
        return artifact_id, self.root_dir / filename

    def put_text(self, content: str, prefix: str = "text") -> ArtifactRef:
        artifact_id, path = self._build_path(prefix, ".txt")
        path.write_text(content, encoding="utf-8")
        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(path),
            media_type="text/plain",
            size_bytes=path.stat().st_size,
            preview=content[: self.max_preview_chars],
        )

    def put_json(self, data: Any, prefix: str = "json") -> ArtifactRef:
        artifact_id, path = self._build_path(prefix, ".json")
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        path.write_text(text, encoding="utf-8")
        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(path),
            media_type="application/json",
            size_bytes=path.stat().st_size,
            preview=text[: self.max_preview_chars],
        )

    def put_bytes(self, blob: bytes, prefix: str = "blob", suffix: str = ".bin", media_type: str = "application/octet-stream") -> ArtifactRef:
        artifact_id, path = self._build_path(prefix, suffix)
        path.write_bytes(blob)
        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(path),
            media_type=media_type,
            size_bytes=path.stat().st_size,
            preview=f"{len(blob)} bytes",
        )

    def read_text(self, artifact_id: str) -> Optional[str]:
        candidate = next(self.root_dir.glob(f"{artifact_id}.*"), None)
        if not candidate or not candidate.exists():
            return None
        return candidate.read_text(encoding="utf-8", errors="replace")
