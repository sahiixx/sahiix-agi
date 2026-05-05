from typing import Any
import aiohttp

async def crypto_price_tracker(symbol: str = "bitcoin", **kwargs: Any) -> str:
    """Fetch the current cryptocurrency price in USD from CoinGecko.

    Args:
        symbol: The CoinGecko ID of the cryptocurrency (e.g., bitcoin, ethereum, cardano).
        **kwargs: Additional keyword arguments for flexibility.

    Returns:
        A formatted string containing the current price in USD, or an error message.
    """
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    return f"Error: HTTP {response.status}"
                data = await response.json()
                price = data.get(symbol, {}).get("usd")
                if price is None:
                    return f"Error: Unable to parse price for {symbol}"
                return f"{symbol.upper()} price: ${price:,.2f} USD"
    except Exception as e:
        return f"Error: {e}"
