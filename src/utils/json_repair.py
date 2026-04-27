"""Multi-pass JSON repair for AI model responses.

Adapted from daily-news-digest. Handles common issues:
- Control characters in strings
- Unescaped quotes inside values
- Trailing commas
- Partial JSON extraction
"""

import json
import re
from typing import Optional


def parse_json_response(response_text: str) -> Optional[dict]:
    """Extract and parse JSON from model response text.

    Uses 4-pass repair strategy:
    1. Direct parse
    2. Fix control chars + unescaped quotes
    3. Remove trailing commas
    4. Line-by-line quote reconstruction

    Returns parsed dict or None.
    """
    start_idx = response_text.find("{")
    end_idx = response_text.rfind("}") + 1
    if start_idx == -1 or end_idx <= start_idx:
        return None
    json_str = response_text[start_idx:end_idx]

    # Pass 1: direct parse
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"  - JSON parse error (attempting fix): {e}")

    # Pass 2: control chars + unescaped quotes
    json_str = re.sub(r"[\x00-\x1f\x7f]", " ", json_str)

    # Common string fields in our JSON schemas
    STRING_FIELDS = (
        "title", "signal", "insight", "implication", "summary",
        "source", "url", "name", "id", "event", "context",
        "why_this_matters", "what_changes", "what_comes_next",
        "self_correction", "trend_summary", "new_key_event",
        "theme_title", "report",
    )
    fields_pattern = "|".join(STRING_FIELDS)

    def fix_quotes_in_value(match):
        key = match.group(1)
        value = match.group(2)
        fixed_value = value.replace('"', "'")
        return f'"{key}": "{fixed_value}"'

    json_str = re.sub(
        rf'"({fields_pattern})"\s*:\s*"((?:[^"\\]|\\.)*)(?<!\\)"',
        fix_quotes_in_value,
        json_str,
    )

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"  - JSON fix attempt 1 failed: {e}")

    # Pass 3: trailing commas
    json_str = re.sub(r",\s*}", "}", json_str)
    json_str = re.sub(r",\s*]", "]", json_str)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Try extracting known top-level arrays
    for key in ("signals", "insights", "updated_trends", "new_trends"):
        try:
            match = re.search(rf'"{key}"\s*:\s*(\[[\s\S]*\])', json_str)
            if match:
                arr_str = match.group(1)
                arr_str = re.sub(r",\s*}", "}", arr_str)
                arr_str = re.sub(r",\s*]", "]", arr_str)
                result = json.loads(arr_str)
                print(f"  - Recovered {len(result)} items from partial JSON (key: {key})")
                return {key: result}
        except Exception:
            continue

    # Pass 4: line-by-line quote reconstruction
    try:
        lines = json_str.split("\n")
        fixed_lines = []
        field_re = re.compile(
            rf'^(\s*"(?:{fields_pattern})"\s*:\s*")(.*)(",?\s*)$'
        )
        for line in lines:
            m = field_re.match(line)
            if m:
                value = m.group(2).replace('"', "'")
                line = m.group(1) + value + m.group(3)
            fixed_lines.append(line)
        json_str = "\n".join(fixed_lines)
        return json.loads(json_str)
    except Exception as e:
        print(f"  - All JSON fix attempts failed: {e}")
        return None
