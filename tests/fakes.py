"""Test doubles imported by test modules (pytest puts tests/ on sys.path)."""


class FakeSearch:
    """Scripted SearchProvider: each call takes the next item; an exception item is raised instead."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def search(self, query, *, max_results, timelimit, region):
        self.calls.append({"query": query, "max_results": max_results, "timelimit": timelimit, "region": region})
        item = self.script.pop(0) if self.script else []
        if isinstance(item, BaseException):
            raise item
        return item
