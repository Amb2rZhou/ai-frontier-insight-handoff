"""AI backend client — supports DeepSeek and Anthropic.

Backend selection:
  - DEEPSEEK_API_KEY set → use DeepSeek (OpenAI-compatible)
  - ANTHROPIC_API_KEY set → use Anthropic Claude
  - Both set → prefer DeepSeek (cheaper for daily use)

DeepSeek models: deepseek-chat (V3)
Anthropic models: Sonnet (primary) + Haiku (fallback)
"""

import os
import time
from typing import Optional

from ..utils.config import load_settings


def _get_backend() -> str:
    """Determine which AI backend to use."""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "none"


# ─── DeepSeek (OpenAI-compatible) ────────────────────────────

def _call_deepseek(prompt: str, label: str, max_tokens: int = 4096) -> Optional[str]:
    """Call DeepSeek API (OpenAI-compatible)."""
    try:
        from openai import OpenAI
    except ImportError:
        print("  Warning: openai package not installed, run: pip3 install openai")
        return None

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    try:
        start = time.time()
        resp = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = time.time() - start
        finish = resp.choices[0].finish_reason
        print(f"  - DeepSeek ({label}) {elapsed:.1f}s, stop={finish}")
        if finish == "length":
            print(f"  - WARNING: Response truncated (hit max_tokens={max_tokens})")
        return resp.choices[0].message.content
    except Exception as e:
        print(f"  - DeepSeek ({label}) error: {e}")
        return None


# ─── Anthropic ───────────────────────────────────────────────

def _call_anthropic(prompt: str, label: str, model: str = None,
                    max_tokens: int = 4096) -> Optional[str]:
    """Call Anthropic Claude API."""
    try:
        import anthropic
    except ImportError:
        print("  Warning: anthropic package not installed")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    if not model:
        settings = load_settings()
        model = settings.get("analysis", {}).get("model_primary", "claude-sonnet-4-20250514")

    client = anthropic.Anthropic(api_key=api_key)

    try:
        start = time.time()
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = time.time() - start
        print(f"  - Claude ({label}) {elapsed:.1f}s, stop={resp.stop_reason}")
        if resp.stop_reason == "max_tokens":
            print(f"  - WARNING: Response truncated (hit max_tokens={max_tokens})")
        return resp.content[0].text
    except Exception as e:
        print(f"  - Claude ({label}) error: {e}")
        return None


# ─── Public API ──────────────────────────────────────────────

def call_sonnet(prompt: str, label: str, max_tokens: int = 4096) -> Optional[str]:
    """Call primary model (DeepSeek or Claude Sonnet)."""
    backend = _get_backend()
    if backend == "deepseek":
        return _call_deepseek(prompt, label, max_tokens)
    elif backend == "anthropic":
        settings = load_settings()
        model = settings.get("analysis", {}).get("model_primary", "claude-sonnet-4-20250514")
        return _call_anthropic(prompt, label, model, max_tokens)
    print("  Warning: No AI API key set (DEEPSEEK_API_KEY or ANTHROPIC_API_KEY)")
    return None


def call_haiku(prompt: str, label: str, max_tokens: int = 4096) -> Optional[str]:
    """Call lightweight model (DeepSeek or Claude Haiku)."""
    backend = _get_backend()
    if backend == "deepseek":
        # DeepSeek only has one model, same as call_sonnet
        return _call_deepseek(prompt, label, max_tokens)
    elif backend == "anthropic":
        settings = load_settings()
        model = settings.get("analysis", {}).get("model_fallback", "claude-haiku-4-5-20251001")
        return _call_anthropic(prompt, label, model, max_tokens)
    print("  Warning: No AI API key set (DEEPSEEK_API_KEY or ANTHROPIC_API_KEY)")
    return None


def call_ai(prompt: str, label: str, use_sonnet: bool = False,
            max_tokens: int = 4096) -> Optional[str]:
    """Call AI with automatic fallback.

    Args:
        prompt: The prompt text
        label: Label for logging
        use_sonnet: If True, try primary model first
        max_tokens: Maximum tokens in response

    Returns response text or None if all backends fail.
    """
    if use_sonnet:
        result = call_sonnet(prompt, label, max_tokens)
        if result:
            return result
        print(f"  - Primary failed for {label}, trying fallback...")
        return call_haiku(prompt, label, max_tokens)
    else:
        result = call_haiku(prompt, label, max_tokens)
        if result:
            return result
        print(f"  - Fallback failed for {label}, trying primary...")
        return call_sonnet(prompt, label, max_tokens)
