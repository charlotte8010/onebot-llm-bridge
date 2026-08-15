from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path, PurePosixPath


def _referenced_files(root: Path) -> list[Path]:
    files = [root / ".env.local", root / ".model_presets.json", root / ".control_panel_theme.json"]
    env_file = root / ".env.local"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() not in {"PERSONA_FILE", "EMOJI_CATALOG", "MEMORY_DB"}:
                continue
            value = value.strip().strip("'\"")
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
            try:
                candidate.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            files.append(candidate)
    return list(dict.fromkeys(path for path in files if path.is_file()))


def create_backup(root: Path, destination: Path | None = None) -> Path:
    """Create a portable backup of local configuration and referenced local data."""
    root = root.resolve()
    if destination is None:
        destination = root / "backups" / f"onebot-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries: list[str] = []
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _referenced_files(root):
            relative = path.resolve().relative_to(root).as_posix()
            archive.write(path, relative)
            entries.append(relative)
        archive.writestr(
            "manifest.json",
            json.dumps({"format": 1, "files": entries}, ensure_ascii=False, indent=2),
        )
    return destination


def restore_backup(root: Path, archive_path: Path) -> list[Path]:
    """Restore only files contained below root; reject zip path traversal."""
    root = root.resolve()
    restored: list[Path] = []
    with zipfile.ZipFile(archive_path, "r") as archive:
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if info.is_dir() or name.is_absolute() or ".." in name.parts or name.name == "manifest.json":
                continue
            target = (root / Path(*name.parts)).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("backup contains an unsafe path") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            restored.append(target)
    return restored
