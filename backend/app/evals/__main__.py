"""`python -m app.evals` → run the eval suite (mock by default; --live for real)."""
from app.evals.harness import main

if __name__ == "__main__":
    raise SystemExit(main())
