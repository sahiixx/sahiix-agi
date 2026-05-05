"""SAHIIX AGI Discord Bot integration using discord.py."""
import asyncio
import os
import logging
from typing import Optional

import discord
import httpx

logger = logging.getLogger(__name__)

AGI_API_BASE = "http://localhost:7777"
DEFAULT_AGENT = "director"
AGENT_EMOJI = {
    "director": "🧠",
    "coder": "💻",
    "researcher": "🔬",
    "sysadmin": "🛠️",
    "architect": "🏗️",
    "dataengineer": "📊",
}
DEFAULT_EMOJI = "🤖"


class DiscordBot(discord.Client):
    """Discord bot for SAHIIX AGI."""

    def __init__(
        self,
        token: Optional[str] = None,
        agi_api_base: str = AGI_API_BASE,
        intents: Optional[discord.Intents] = None,
    ):
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True
            intents.dm_messages = True
        super().__init__(intents=intents)
        self.token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self.agi_api_base = agi_api_base
        self._running = False

    async def _agi_chat(self, message: str, agent: str = DEFAULT_AGENT) -> str:
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
                    f"**SAHIIX AGI Status**\n"
                    f"• Agents: {', '.join(f'`{a}`' for a in agents) if agents else 'none'}\n"
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
                    f"**SAHIIX AGI Agents**\n"
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
                    return "**SAHIIX AGI Missions**\nNo active missions."
                lines = [f"**SAHIIX AGI Missions** ({len(missions)} total)"]
                for m in missions[:5]:
                    lines.append(f"• `{m.get('id', '?')}` — {m.get('goal', '?')[:60]}…")
                return "\n".join(lines)
        except Exception as e:
            logger.exception("AGI missions fetch failed")
            return f"[SAHIIX AGI Error] {e}"

    async def _handle_command(self, content: str) -> Optional[str]:
        text = content.strip()
        if not text.startswith("!sahiix"):
            return None
        parts = text[len("!sahiix"):].strip().split()
        sub = parts[0].lower() if parts else ""
        if sub in ("status", "st"):
            return await self._agi_status()
        if sub in ("agents", "agent", "a"):
            return await self._agi_agents()
        if sub in ("missions", "mission", "m"):
            return await self._agi_missions()
        return (
            "**SAHIIX AGI Commands**\n"
            "• `!sahiix status` — system status\n"
            "• `!sahiix agents` — list agents\n"
            "• `!sahiix mission` — list missions"
        )

    async def on_ready(self):
        logger.info("Discord bot logged in as %s (id=%s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Handle commands
        cmd_response = await self._handle_command(message.content)
        if cmd_response is not None:
            await message.channel.send(cmd_response)
            return

        # Only respond to mentions in guild channels, or any message in DMs
        if isinstance(message.channel, discord.DMChannel):
            pass  # respond to all DMs
        elif not self.user or not self.user.mentioned_in(message):
            return

        text = message.content
        if self.user:
            # crude strip of mention string(s)
            mention_str = f"<@{self.user.id}>"
            text = text.replace(mention_str, "").strip()

        if not text:
            return

        response_text = await self._agi_chat(text)
        emoji = AGENT_EMOJI.get(DEFAULT_AGENT, DEFAULT_EMOJI)
        prefixed = f"{emoji} **SAHIIX AGI** (`{DEFAULT_AGENT}`):\n{response_text}"
        await message.channel.send(prefixed[:2000])  # Discord 2000 char limit

    async def start_bot(self):
        if not self.token:
            logger.error("DISCORD_BOT_TOKEN is required.")
            return
        self._running = True
        await self.start(self.token)

    async def stop_bot(self):
        self._running = False
        await self.close()
        logger.info("Discord bot stopped.")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bot = DiscordBot()
    try:
        await bot.start_bot()
    except asyncio.CancelledError:
        await bot.stop_bot()


if __name__ == "__main__":
    asyncio.run(main())
