"""
One-off diagnostic: ask Google directly which models your API key can
actually use right now, instead of guessing a hardcoded name that might
be retired by the time you read this.

Run from repo root:
    python -m code.ingestion.list_gemini_models

Look at the printed list, pick one (prefer a "flash-lite" or "flash"
variant for cost/quota reasons over "pro"), then set it in .env:
    LLM_MODEL=<whatever name it printed>
"""

from __future__ import annotations

import os
from dotenv import load_dotenv


def main():
    load_dotenv()
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    print("Models available to your key that support generateContent "
          "(i.e. usable for our vision/text calls):\n")
    for model in genai.list_models():
        if "generateContent" in model.supported_generation_methods:
            print(f"  {model.name}")

    print("\nPick one of the names above (WITHOUT the 'models/' prefix), "
          "prefer a flash-lite or flash variant, and set it in .env as:")
    print("  LLM_MODEL=<name>")


if __name__ == "__main__":
    main()
