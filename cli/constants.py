# Guards recursive traversal of user-controlled YAML structures (schema
# defaults, contract/config interpolation) against maliciously or accidentally
# deeply nested documents that would otherwise raise an unhandled
# RecursionError / stack overflow.
MAX_NESTING_DEPTH = 100

class MaxNestingDepthExceeded(Exception):
    """Raised when a recursive structure exceeds MAX_NESTING_DEPTH."""
