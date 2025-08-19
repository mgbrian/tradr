# Copy into env.py and update accordingly.
import os

# Interactive Brokers Settings
# Leave as localhost if TWS is running on the same machine, else update.
os.environ.setdefault("IB_HOST", "localhost")
# Usual defaults - TWS paper: 7497, TWS live: 7496, IBG paper: 4002, IBG live: 4001
os.environ.setdefault("IB_PORT", "")
 # An arbitrary number unique across connected clients.
 # Set this to "0" to receive orders made in the TWS GUI.
os.environ.setdefault("IB_CLIENT_ID", "0")

# Make this a long, unpredictable value.
os.environ.setdefault("DJANGO_SECRET_KEY", "")

os.environ.setdefault("GRPC_SERVER_ADDRESS", f"[::]:{50057}")

# Postgres Settings
# Set this to "1" to persist data to the persistent DB (and provide Postgres details)
# If set to "0" or not set, data is only stored in the (ephemeral) in-memory db
os.environ.setdefault("USE_PERSISTENT_DB", "")
os.environ.setdefault("POSTGRES_DB_USER", "")
os.environ.setdefault("POSTGRES_DB_PASSWORD", "")
os.environ.setdefault("POSTGRES_HOST", "")
os.environ.setdefault("POSTGRES_PORT", "")
