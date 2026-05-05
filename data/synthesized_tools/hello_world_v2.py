async def hello_world_v2(**kwargs) -> str:
    """Return a Hello World string.

    Args:
        **kwargs: Additional keyword arguments (ignored).

    Returns:
        str: A greeting message or an error description.
    """
    try:
        return "Hello World"
    except Exception as e:
        return f"Error: {e}"