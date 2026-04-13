"""
Unified LLM API client. ALL LLM calls in the system go through this module.
To switch from API to local inference, modify only this file.
"""

import os
import json
import time
import anthropic
from typing import Optional

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

# Pin model versions for reproducibility
MODELS = {
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
    "haiku": "claude-haiku-4-5-20251001",
}


def generate(
    prompt: str,
    model: str = "sonnet",
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    response_format: str = "json",
    max_retries: int = 3,
) -> dict:
    """
    Make an LLM API call with retry logic and JSON validation.

    Args:
        prompt: The user message content
        model: "sonnet", "opus", or "haiku"
        system: System prompt
        temperature: Sampling temperature
        max_tokens: Max response tokens
        response_format: "json" or "text"
        max_retries: Number of retry attempts

    Returns:
        Parsed JSON dict if response_format="json", else {"text": raw_text}
    """
    model_id = MODELS.get(model, model)
    raw_text = ""

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system if system else "",
                messages=[{"role": "user", "content": prompt}],
            )

            raw_text = response.content[0].text

            if response_format == "json":
                # Strip markdown code fences if present
                cleaned = raw_text.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()

                parsed = json.loads(cleaned)
                parsed["_meta"] = {
                    "model": model_id,
                    "timestamp": time.time(),
                    "attempt": attempt + 1,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
                return parsed
            else:
                return {
                    "text": raw_text,
                    "_meta": {
                        "model": model_id,
                        "timestamp": time.time(),
                        "attempt": attempt + 1,
                    },
                }

        except json.JSONDecodeError as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise ValueError(
                f"Failed to parse JSON after {max_retries} attempts: {e}\n"
                f"Raw: {raw_text[:500]}"
            )

        except anthropic.RateLimitError:
            wait = 2 ** (attempt + 2)
            time.sleep(wait)
            continue

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    raise RuntimeError(f"LLM API call failed after {max_retries} retries (last error: rate limited)")
