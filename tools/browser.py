"""Browser automation tool using Playwright async API."""
import asyncio
import hashlib
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from tools.registry import ToolResult

# ── Optional Playwright ───────────────────────────────────────────────────────
try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    _PW_AVAILABLE = True
except Exception:
    _PW_AVAILABLE = False
    Page = Any
    Browser = Any
    BrowserContext = Any


# ── Safety helpers ────────────────────────────────────────────────────────────

_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
}

_BLOCKED_SCHEMES = {"file", "ftp", "javascript", "data", "vbscript"}


def _is_dangerous_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme in _BLOCKED_SCHEMES:
            return True
        hostname = parsed.hostname or ""
        if hostname in _BLOCKED_HOSTS:
            return True
        # Block private IP ranges
        parts = hostname.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            if parts[0] == "10":
                return True
            if parts[0] == "172" and 16 <= int(parts[1]) <= 31:
                return True
            if parts[0] == "192" and parts[1] == "168":
                return True
            if parts[0] == "127":
                return True
            if parts[0] == "0":
                return True
        return False
    except Exception:
        return True


# ── Browser Tool ──────────────────────────────────────────────────────────────

class BrowserTool:
    """Async browser automation with Playwright. Safe by default."""

    ACTION_TIMEOUT_MS = 30000

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()
        self._closed = True

    async def _ensure(self):
        if not _PW_AVAILABLE:
            raise RuntimeError("Playwright is not installed. Run: playwright install")
        if self._browser and not self._closed:
            return
        async with self._lock:
            if self._browser and not self._closed:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="SAHIIX-AGI-Bot/1.0 (+https://sahiix.ai/bot)",
            )
            self._page = await self._context.new_page()
            self._closed = False

    async def close(self):
        async with self._lock:
            if self._page:
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = None
            if self._context:
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            self._closed = True

    async def navigate(self, url: str) -> ToolResult:
        """Navigate to a URL. Params: url (str)."""
        if _is_dangerous_url(url):
            return ToolResult(False, "", f"Blocked dangerous URL: {url}")
        try:
            await self._ensure()
            response = await self._page.goto(url, timeout=self.ACTION_TIMEOUT_MS, wait_until="domcontentloaded")
            title = await self._page.title()
            return ToolResult(
                True,
                f"Navigated to {url}. Title: {title}",
                metadata={"status": response.status if response else None, "title": title}
            )
        except Exception as e:
            return ToolResult(False, "", str(e))

    async def screenshot(self, path: Optional[str] = None) -> ToolResult:
        """Take a screenshot of the current page. Params: path (str, optional)."""
        try:
            await self._ensure()
            ts = int(time.time())
            default_path = f"/tmp/sahiix_screenshot_{ts}.png"
            out_path = path or default_path
            await self._page.screenshot(path=out_path, timeout=self.ACTION_TIMEOUT_MS)
            return ToolResult(True, f"Screenshot saved to {out_path}", metadata={"path": out_path})
        except Exception as e:
            return ToolResult(False, "", str(e))

    async def click(self, selector: str) -> ToolResult:
        """Click an element by CSS selector. Params: selector (str)."""
        try:
            await self._ensure()
            await self._page.click(selector, timeout=self.ACTION_TIMEOUT_MS)
            return ToolResult(True, f"Clicked {selector}")
        except Exception as e:
            return ToolResult(False, "", str(e))

    async def fill(self, selector: str, text: str) -> ToolResult:
        """Fill an input field. Params: selector (str), text (str)."""
        try:
            await self._ensure()
            await self._page.fill(selector, text, timeout=self.ACTION_TIMEOUT_MS)
            return ToolResult(True, f"Filled {selector}")
        except Exception as e:
            return ToolResult(False, "", str(e))

    async def extract_text(self, selector: Optional[str] = None) -> ToolResult:
        """Extract text from the page or a specific selector. Params: selector (str, optional)."""
        try:
            await self._ensure()
            if selector:
                elements = await self._page.query_selector_all(selector)
                texts = []
                for el in elements:
                    t = await el.inner_text()
                    if t:
                        texts.append(t.strip())
                output = "\n".join(texts)[:5000]
            else:
                output = await self._page.inner_text("body")
                output = output[:5000]
            return ToolResult(True, output, metadata={"length": len(output)})
        except Exception as e:
            return ToolResult(False, "", str(e))

    async def crawl(self, start_url: str, max_depth: int = 2) -> ToolResult:
        """Crawl links from a starting URL up to a max depth. Params: start_url (str), max_depth (int, default 2)."""
        if _is_dangerous_url(start_url):
            return ToolResult(False, "", f"Blocked dangerous URL: {start_url}")
        try:
            await self._ensure()
            visited: Set[str] = set()
            queue: List[tuple[str, int]] = [(start_url, 0)]
            results: List[dict] = []

            while queue:
                url, depth = queue.pop(0)
                if url in visited or depth > max_depth or _is_dangerous_url(url):
                    continue
                visited.add(url)
                try:
                    await self._page.goto(url, timeout=self.ACTION_TIMEOUT_MS, wait_until="domcontentloaded")
                    title = await self._page.title()
                    links = await self._page.eval_on_selector_all(
                        "a[href]", "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
                    )
                    results.append({"url": url, "depth": depth, "title": title, "links": list(links)[:20]})
                    if depth < max_depth:
                        for link in links:
                            if link not in visited and not _is_dangerous_url(link):
                                queue.append((link, depth + 1))
                except Exception as e:
                    results.append({"url": url, "depth": depth, "error": str(e)})

            return ToolResult(
                True,
                f"Crawled {len(visited)} pages up to depth {max_depth}.",
                metadata={"pages": results}
            )
        except Exception as e:
            return ToolResult(False, "", str(e))

    # Registry adapter
    async def __call__(self, action: str = "navigate", **kwargs) -> ToolResult:
        """Dispatch to a browser action by name."""
        mapping = {
            "navigate": self.navigate,
            "screenshot": self.screenshot,
            "click": self.click,
            "fill": self.fill,
            "extract_text": self.extract_text,
            "crawl": self.crawl,
        }
        fn = mapping.get(action)
        if not fn:
            return ToolResult(False, "", f"Unknown browser action: {action}")
        return await fn(**kwargs)
