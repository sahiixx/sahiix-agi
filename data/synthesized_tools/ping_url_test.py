async def ping_url_test(url: str = "https://example.com", **kwargs) -> str:
    """Ping a URL and return the HTTP status.

    Args:
        url: The URL to ping.
        **kwargs: Additional keyword arguments for flexibility.

    Returns:
        A string containing the HTTP status code or an error message.
    """
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return f"Status: {response.status}"
    except Exception as e:
        return f"Error: {e}"