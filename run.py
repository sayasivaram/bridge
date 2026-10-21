"""Start Closer locally: python run.py  (then open http://127.0.0.1:8000)"""

import uvicorn

if __name__ == "__main__":
    print("Closer is running at http://127.0.0.1:8000  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
