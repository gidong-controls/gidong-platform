"""Client request input policy, separate from any provider capacity claim."""
from __future__ import annotations

MAX_CLIENT_INPUT_LIMIT_TOKENS = 9_007_199_254_740_991


def validate_client_input_limit_tokens(value: object) -> int | None:
    """Null means Auto; booleans, coercions, zero and negatives are rejected."""
    if value is None:
        return None
    if type(value) is not int or not 0 < value <= MAX_CLIENT_INPUT_LIMIT_TOKENS:
        raise ValueError('client_input_limit_tokens must be null or a positive integer <= 9007199254740991')
    return value
