# Copy into env.py and update accordingly.
import os

os.environ.setdefault("DJANGO_SECRET_KEY", "<some long string>")

os.environ.setdefault("POSTGRES_DB_USER", "")
os.environ.setdefault("POSTGRES_DB_PASSWORD", "")
os.environ.setdefault("POSTGRES_HOST", "")
os.environ.setdefault("POSTGRES_PORT", "")
