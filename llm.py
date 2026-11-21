import os
from pathlib import Path

from crewai import LLM
from dotenv import dotenv_values


class ConfigurationError(Exception):
    pass


def create_llm():
    config = dotenv_values(Path(__file__).with_name(".env"), encoding="utf-8-sig")
    model = _setting(config, "LLM_MODEL")
    api_key = _setting(config, "LLM_API_KEY")
    base_url = _setting(config, "LLM_BASE_URL")
    if not model or not api_key:
        raise ConfigurationError("Set LLM_MODEL and LLM_API_KEY in your .env file.")
    options = {"base_url": base_url, "custom_openai": True} if base_url else {}
    return LLM(model=model, api_key=api_key, timeout=30, **options)


def _setting(config, key):
    return os.getenv(key, "").strip() or (config.get(key) or "").strip()
