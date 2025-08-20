"""Script to test connection with TWS or IB Gateway.

Update settings below and run:

    source .requirements/bin/activate && python setup_test.py
"""
from ib_async import IB


# Connection settings -- adjust as needed
HOST = '127.0.0.1'  # Usually localhost
PORT = 7497  # Default for TWS paper trading; 7496 for live
CLIENT_ID = 1 # Arbitrary, but must be unique per client connection


def main():
    ib = IB()
    print(f"Attempting to connect to IB at {HOST}:{PORT} with clientId={CLIENT_ID}...")

    try:
        ib.connect(HOST, PORT, CLIENT_ID, timeout=5)

    except Exception as e:
        print(f"Connection failed: {e}")
        return

    if ib.isConnected():
        print("Successfully connected to IB.")

        accounts = ib.managedAccounts()
        print(f"Managed accounts: {accounts}")

    else:
        print("Failed to connect.")

    ib.disconnect()


if __name__ == "__main__":
    main()
