"""Start Closer locally: python run.py  (then open http://127.0.0.1:8000)"""

import sys

import uvicorn

from app.config import MissingAPIKeyError, load_settings

if __name__ == "__main__":
    settings = load_settings()
    try:
        settings.require_api_key()
    except MissingAPIKeyError as err:
        sys.exit(f"\n{err}\n")
    print(f"Using {settings.provider} key from {settings.key_source}, model {settings.model}.")
    print("Closer is running at http://127.0.0.1:8000  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
