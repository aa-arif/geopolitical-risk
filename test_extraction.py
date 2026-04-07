"""
Test script: run a single article through track_b/extraction.py
and print the full JSON reasoning chain output.
"""

import sys
import json

sys.path.insert(0, ".")

from config.settings import load_country_config
from track_b.extraction import extract_reasoning

# Load Nigeria config
country_config = load_country_config("nigeria")

# Load test article
with open("data/articles/test_nigeria_aljazeera.txt", "r") as f:
    article_text = f.read()

print("=" * 70)
print("EXTRACTION TEST: Nigeria - Al Jazeera article")
print("Article length:", len(article_text), "chars")
print("=" * 70)
print()

# Run extraction
result = extract_reasoning(article_text, country_config)

# Pretty-print the full JSON output
print(json.dumps(result, indent=2, ensure_ascii=False))
