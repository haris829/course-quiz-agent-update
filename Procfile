web: ls -l reference_data.json.gz 2>&1 | head -1; python -m qgen load-reference || true; python -m uvicorn qgen.asgi:app --host 0.0.0.0 --port $PORT
