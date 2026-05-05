from typing import Any
import aiohttp

async def btc_price(**kwargs: Any) -> str:
    """Fetch the current Bitcoin price in USD from CoinGecko.

    Args:
        **kwargs: Additional keyword arguments for flexibility.

    Returns:
        A formatted string containing the current BTC price in USD, or an error message.
    """
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    return f"Error: HTTP {response.status}"
                data = await response.json()
                price = data.get("bitcoin", {}).get("usd")
                if price is None:
                    return "Error: Unable to parse BTC price from response"
                return f"Bitcoin price: ${price:,.2f} USD"
    except Exception as e:
        return f"Error: {e}"