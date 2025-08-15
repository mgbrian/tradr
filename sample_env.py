# Copy into env.py and update accordingly.
import os

# Interactive Brokers Settings
os.environ.setdefault("IB_HOST", "") # Usually localhost.
os.environ.setdefault("IB_PORT", "") # Usual defaults: 7497 - paper trading, 7496 - live.
os.environ.setdefault("IB_CLIENT_ID", "") # An arbitrary number unique across connected clients. "1" is OK if this is the only one.

# Make this a long, unpredictable value.
os.environ.setdefault("DJANGO_SECRET_KEY", "")

os.environ.setdefault("GRPC_SERVER_ADDRESS", f"[::]:{50057}")

# Postgres Settings
os.environ.setdefault("POSTGRES_DB_USER", "")
os.environ.setdefault("POSTGRES_DB_PASSWORD", "")
os.environ.setdefault("POSTGRES_HOST", "")
os.environ.setdefault("POSTGRES_PORT", "")
