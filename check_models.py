from __future__ import annotations

import argparse
import os
from pathlib import Path

from onebot_llm_bridge.config import Settings, load_env_file, merged_environment
from onebot_llm_bridge.providers import OpenAICompatibleProvider, ProviderError


ROOT = Path(__file__).resolve().parent


def settings_from_file(path: Path | None) -> Settings:
    values = merged_environment(ROOT)
    if path is not None:
        values = {**load_env_file(path), **dict(os.environ)}
    return Settings.from_values(values)


def provider_for(settings: Settings, *, vision: bool = False) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key=settings.vision_api_key if vision else settings.llm_api_key,
        base_url=settings.vision_base_url if vision else settings.llm_base_url,
        model=settings.vision_model if vision else settings.llm_model,
        max_tokens=settings.vision_max_tokens if vision else settings.llm_max_tokens,
        timeout=settings.vision_timeout_seconds if vision else settings.llm_timeout_seconds,
    )


def show_models(label: str, provider: OpenAICompatibleProvider) -> None:
    print(f"[{label}]")
    print(f"Base URL: {provider.base_url}")
    print(f"Configured model: {provider.model}")
    try:
        models = provider.list_models()
    except ProviderError as exc:
        print(f"/models check failed: {exc}")
        return
    if models:
        print("Available models:")
        for model in models:
            print(f"  - {model}")
    else:
        print("/models returned no model IDs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check OpenAI-compatible chat and vision model APIs.")
    parser.add_argument("--env-file", type=Path, default=None, help="dotenv file, default: .env.local/.env")
    args = parser.parse_args()
    settings = settings_from_file(args.env_file)
    settings.validate_for_bot()
    show_models("chat", provider_for(settings))
    if settings.vision_mode == "separate":
        show_models("vision", provider_for(settings, vision=True))
    else:
        print(f"[vision] mode={settings.vision_mode}; separate vision API is not enabled")


if __name__ == "__main__":
    main()
