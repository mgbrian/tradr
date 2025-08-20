"""Useful utilities used by multiple test modules."""

class FakeEvent:
    """ib_async.Event test double supporting +=, -=, remove(), len(), and indexing."""
    def __init__(self):
        self._handlers = []

    # Support self.event += handler
    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    # Support self.event -= handler
    def __isub__(self, handler):
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass
        return self

    def emit(self, *args, **kwargs):
        for h in list(self._handlers):
            h(*args, **kwargs)

    # For code paths that call .remove(handler)
    def remove(self, handler):
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass

    # Let tests do len(event) and event[0]
    def __len__(self):
        return len(self._handlers)

    def __getitem__(self, idx):
        return self._handlers[idx]

    # Handy for assertions
    @property
    def handler_count(self):
        return len(self._handlers)
