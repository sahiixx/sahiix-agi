async def hello_world_test(**kwargs: object) -> str:
    """Return hello world.

    Args:
        **kwargs: Additional keyword arguments (unused).

    Returns:
        str: Hello World string.
    """
    try:
        return "Hello World"
    except Exception as e:
        return f"Error: {e}"