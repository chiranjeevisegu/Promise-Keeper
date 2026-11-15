"""Exercise the configured classifier and local extraction service with a known commitment."""
import os

from app.commitment_graph import DEFAULT_OLLAMA_MODEL, extract
from app.commitment_model import LABELS, predict


def main() -> int:
    item = {
        "speaker": "Priya",
        "context": "Sam: Who will send the checklist?",
        "quote": "I'll send the deployment checklist to Sam by Friday.",
    }
    try:
        scores, = predict([item])
        if set(scores) != set(LABELS) or abs(sum(scores.values()) - 1) > 1e-5:
            raise RuntimeError("Classifier returned invalid probabilities.")
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(f"Classifier check failed: {error}")
        return 1
    print("Classifier inference passed.")
    model = os.getenv("PROMISE_KEEPER_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    result = extract({"candidates": [item]})
    if result["extraction_unavailable"]:
        print(f"Live extraction failed. Check that Ollama is running with {model}.")
        return 1
    details = result["candidates"][0]["details"]
    expected = {"owner": "priya", "task": "checklist", "recipient": "sam", "deadline": "friday"}
    missing = [field for field, token in expected.items() if token not in (details[field] or "").casefold()]
    if missing:
        print("Live extraction missed expected fields: " + ", ".join(missing))
        return 1
    print("Live extraction passed for owner, task, recipient, and deadline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
