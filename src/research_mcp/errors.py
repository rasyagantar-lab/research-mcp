"""Error codes shared by every tool. A ToolError becomes an isError tool result, never a traceback."""

CODES = frozenset({
    "invalid_url",
    "invalid_argument",
    "blocked_private_address",
    "blocked_by_site",
    "http_error",
    "timeout",
    "too_large",
    "unsupported_content",
    "not_text",
    "rate_limited",
    "offset_out_of_range",
    "render_unavailable",
    "save_failed",
    "internal_error",
})


class ToolError(Exception):
    """A failure the model should read: what went wrong and what to try next."""

    def __init__(self, code: str, message: str, suggestion: str) -> None:
        if code not in CODES:
            raise ValueError(f"unknown error code: {code}")
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.suggestion = suggestion

    def render(self) -> str:
        return f"Error [{self.code}]: {self.message.rstrip('.')}. Suggestion: {self.suggestion}"
