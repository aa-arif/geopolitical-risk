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

        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                wait = 30 * (attempt + 1)  # 30s, 60s, 90s
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
                raise
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    raise RuntimeError(f"LLM API call failed after {max_retries} retries (last error: rate limited)")


def generate_with_tool(
    prompt: str,
    tool_name: str,
    tool_schema: dict,
    model: str = "sonnet",
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    max_retries: int = 3,
) -> dict:
    """
    Make an LLM API call that forces structured output via tool_use.

    Defines a single tool with the given schema and forces the model to call it,
    guaranteeing the response matches the schema (types, ranges, enums).

    Falls back to free-text JSON parsing if tool_use fails.

    Args:
        prompt: The user message content
        tool_name: Name of the tool (e.g., "submit_forecast")
        tool_schema: JSON Schema for the tool's input_schema
        model: "sonnet", "opus", or "haiku"
        system: System prompt
        temperature: Sampling temperature
        max_tokens: Max response tokens
        max_retries: Number of retry attempts

    Returns:
        Parsed dict matching the tool schema
    """
    model_id = MODELS.get(model, model)

    tool_def = {
        "name": tool_name,
        "description": f"Submit your {tool_name.replace('_', ' ')} output.",
        "input_schema": tool_schema,
    }

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system if system else "",
                messages=[{"role": "user", "content": prompt}],
                tools=[tool_def],
                tool_choice={"type": "tool", "name": tool_name},
            )

            # Extract tool_use block from response
            for block in response.content:
                if block.type == "tool_use" and block.name == tool_name:
                    result = block.input
                    result["_meta"] = {
                        "model": model_id,
                        "timestamp": time.time(),
                        "attempt": attempt + 1,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "structured": True,
                    }
                    return result

            # No tool_use block found -- fall back to text parsing
            raw_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    raw_text += block.text

            if raw_text.strip():
                cleaned = raw_text.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                parsed = json.loads(cleaned.strip())
                parsed["_meta"] = {
                    "model": model_id,
                    "timestamp": time.time(),
                    "attempt": attempt + 1,
                    "structured": False,
                }
                return parsed

            raise ValueError("No tool_use block or parseable text in response")

        except anthropic.RateLimitError:
            wait = 2 ** (attempt + 2)
            time.sleep(wait)
            continue

        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                wait = 30 * (attempt + 1)
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
                raise
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

        except (json.JSONDecodeError, ValueError) as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise ValueError(f"Structured output failed after {max_retries} attempts: {e}")

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    raise RuntimeError(f"generate_with_tool failed after {max_retries} retries")
