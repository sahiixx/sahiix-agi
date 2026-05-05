from typing import Any

async def hello_test(**kwargs: Any) -> str:
    """Return a simple hello greeting.

    Args:
        **kwargs: Additional keyword arguments (ignored).

    Returns:
        str: The greeting "hello".
    """
    try:
        return "hello"
    except Exception as e:
        return f"Error: {e}"