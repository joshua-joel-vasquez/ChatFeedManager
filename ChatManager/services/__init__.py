"""ChatManager microservices package.

Having this as a proper package allows the launcher to start microservices via:

  python -m services.ingestor

which ensures imports like `from shared...` work reliably.
"""
