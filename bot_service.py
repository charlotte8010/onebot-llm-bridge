from __future__ import annotations

import argparse
import os
from pathlib import Path

from onebot_llm_bridge.config import Settings, load_env_file, merged_environment
from onebot_llm_bridge.services import serve_bot


ROOT = Path(__file__).resolve().parent


def settings_from_file(path: Path | None) -> Settings:
    values = merged_environment(ROOT)
    if path is not None:
        values = {**load_env_file(path), **dict(os.environ)}
    return Settings.from_values(values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the model-backed bot service.")
    parser.add_argument("--env-file", type=Path, default=None, help="dotenv file, default: .env.local/.env")
    args = parser.parse_args()
    settings = settings_from_file(args.env_file)
    serve_bot(settings)


if __name__ == "__main__":
    main()

