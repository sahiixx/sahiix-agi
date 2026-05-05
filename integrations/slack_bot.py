"""SAHIIX AGI Slack Bot integration using slack-sdk async."""
import asyncio
import os
import logging
from typing import Optional

import httpx
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest

logger = logging.getLogger(__name__)

AGI_API_BASE = "http://localhost:7777"
DEFAULT_AGENT = "director"


class SlackBot:
    """Async Slack bot for SAHIIX AGI."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        app_token: Optional[str] = None,
        agi_api_base: str = AGI_API_BASE,
    ):
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self.app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        self.agi_api_base = agi_api_base
        self.client: Optional[AsyncWebClient] = None
        self.socket_client: Optional[SocketModeClient] = None
        self._running = False

    async def _agi_chat(self, message: str, agent: str = DEFAULT_AGENT) -> str:
        """Send message to SAHIIX AGI HTTP API and return response text."""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.agi_api_base}/api/chat",
                    json={"message": message, "agent": agent},
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "_No response_")
        except Exception as e:
            logger.exception("AGI API chat failed")
            return f"[SAHIIX AGI Error] {e}"

    async def _agi_status(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.agi_api_base}/api/status")
                resp.raise_for_status()
                data = resp.json()
                agents = data.get("agents", [])
                return (
                    f"*SAHIIX AGI Status*\n"
                    f"• Agents: {', '.join(agents) if agents else 'none'}\n"
                    f"• API latency: {data.get('api_latency_ms', '?')} ms\n"
                    f"• Autonomy: {data.get('autonomy', {}).get('enabled', '?')}"
                )
        except Exception as e:
            logger.exception("AGI status fetch failed")
            return f"[SAHIIX AGI Error] {e}"

    async def _agi_agents(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.agi_api_base}/api/agents")
                resp.raise_for_status()
                data = resp.json()
                agents = data.get("agents", [])
                return (
                    f"*SAHIIX AGI Agents*\n"
                    + "\n".join(f"• `{a}`" for a in agents)
                    if agents
                    else "No agents available."
                )
        except Exception as e:
            logger.exception("AGI agents fetch failed")
            return f"[SAHIIX AGI Error] {e}"

    async def _agi_missions(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.agi_api_base}/api/missions")
                resp.raise_for_status()
                data = resp.json()
                missions = data.get("missions", [])
                if not missions:
                    return "*SAHIIX AGI Missions*\nNo active missions."
                lines = [f"*SAHIIX AGI Missions* ({len(missions)} total)"]
                for m in missions[:5]:
                    lines.append(f"• `{m.get('id', '?')}` — {m.get('goal', '?')[:60]}…")
                return "\n".join(lines)
        except Exception as e:
            logger.exception("AGI missions fetch failed")
            return f"[SAHIIX AGI Error] {e}"

    async def _handle_command(self, command: str, text: str) -> str:
        cmd = (command or "").strip().lower()
        body = (text or "").strip().lower()
        if cmd == "/sahiix" or cmd.startswith("/sahiix "):
            sub = body or cmd.replace("/sahiix", "").strip()
            if sub in ("status", "st"):
                return await self._agi_status()
            if sub in ("agents", "agent", "a"):
                return await self._agi_agents()
            if sub in ("missions", "mission", "m"):
                return await self._agi_missions()
            return (
                "*SAHIIX AGI Commands*\n"
                "• `/sahiix status` — system status\n"
                "• `/sahiix agents` — list agents\n"
                "• `/sahiix mission` — list missions"
            )
        return ""

    async def _process_event(self, request: SocketModeRequest):
        payload = request.payload
        event = payload.get("event", {})
        event_type = event.get("type", "")

        # Handle slash commands delivered via socket mode (envelope_id path)
        if payload.get("command"):
            command = payload.get("command", "")
            text = payload.get("text", "")
            channel_id = payload.get("channel_id", "")
            response = await self._handle_command(command, text)
            if response:
                await self.client.chat_postMessage(
                    channel=channel_id,
                    text=response,
                )
            return

        if event_type not in ("app_mention", "message"):
            return

        # Ignore bot messages / message_changed subtypes
        subtype = event.get("subtype")
        if subtype in ("bot_message", "message_changed", "message_deleted"):
            return
        if event.get("bot_id") or event.get("user") == "USLACKBOT":
            return

        user = event.get("user", "unknown")
        text = event.get("text", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")

        # Strip bot mention from text
        if event_type == "app_mention":
            # crude strip of the <@BOT_ID> mention
            import re
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

        if not text:
            return

        response_text = await self._agi_chat(text)
        prefixed = f"*SAHIIX AGI (`{DEFAULT_AGENT}`)*:\n{response_text}"

        try:
            await self.client.chat_postMessage(
                channel=channel,
                text=prefixed,
                thread_ts=thread_ts,
            )
        except Exception:
            logger.exception("Failed to post Slack message")

    async def _socket_listener(self, client: SocketModeClient, request: SocketModeRequest):
        if request.type == "events_api" or request.type == "slash_commands":
            # Acknowledge
            response = SocketModeResponse(envelope_id=request.envelope_id)
            await client.send_socket_mode_response(response)
            await self._process_event(request)

    async def start(self):
        if not self.bot_token or not self.app_token:
            logger.error("SLACK_BOT_TOKEN and SLACK_APP_TOKEN are required.")
            return
        self.client = AsyncWebClient(token=self.bot_token)
        self.socket_client = SocketModeClient(
            app_token=self.app_token,
            web_client=self.client,
        )
        self.socket_client.socket_mode_request_listeners.append(self._socket_listener)
        self._running = True
        logger.info("Starting Slack Socket Mode client…")
        await self.socket_client.connect()
        # Keep alive
        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        self._running = False
        if self.socket_client:
            await self.socket_client.close()
        logger.info("Slack bot stopped.")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bot = SlackBot()
    try:
        await bot.start()
    except asyncio.CancelledError:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
