"""n8n Bridge for SAHIIX AGI — bidirectional webhook and workflow integration."""
import os
from typing import Any, Dict, List, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except Exception:
    HTTPX_AVAILABLE = False


class N8nBridge:
    """Bidirectional integration with n8n workflow automation."""

    def __init__(
        self,
        base_url: str = "http://localhost:5678",
        api_key: Optional[str] = None,
    ):
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx is not installed")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("N8N_API_KEY")
        self.webhooks: Dict[str, Dict[str, Any]] = {}
        self._client = httpx.AsyncClient(timeout=30.0)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        return headers

    def register_webhook(
        self,
        name: str,
        url: str,
        tool_name: Optional[str] = None,
        tool_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.webhooks[name] = {
            "url": url,
            "tool_name": tool_name,
            "tool_params": tool_params or {},
        }
        return {"name": name, "url": url, "registered": True}

    async def trigger_n8n(self, webhook_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = await self._client.post(
                webhook_url,
                json=payload,
                headers=self._headers(),
                timeout=30.0,
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            data = resp.json() if content_type.startswith("application/json") else resp.text
            return {"success": True, "status_code": resp.status_code, "data": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_workflows(self) -> Dict[str, Any]:
        try:
            url = f"{self.base_url}/api/v1/workflows"
            resp = await self._client.get(url, headers=self._headers(), timeout=30.0)
            resp.raise_for_status()
            body = resp.json()
            return {"success": True, "workflows": body.get("data", [])}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def handle_webhook(
        self,
        name: str,
        payload: Dict[str, Any],
        director=None,
    ) -> Dict[str, Any]:
        mapping = self.webhooks.get(name)
        if not mapping:
            return {"error": f"Webhook '{name}' not registered"}
        if director and mapping.get("tool_name"):
            merged_params = {**mapping.get("tool_params", {}), **payload}
            result = await director.tools.execute(mapping["tool_name"], **merged_params)
            return {
                "success": result.success,
                "output": result.output,
                "error": result.error,
            }
        return {"received": True, "payload": payload}

    async def close(self):
        await self._client.aclose()
