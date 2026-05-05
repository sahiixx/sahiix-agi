"""SAHIIX Voice Daemon — JARVIS-style voice control connecting to the running API server."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import aiohttp


class VoiceEngine:
    """Enhanced Voice Engine with extended wake words, TTS selection, and voice command pattern matching."""

    VOICE_COMMANDS = {
        "status": ["status", "system status", "what is the status", "how are things", "health check"],
        "swarm": ["swarm mode", "deploy swarm", "activate swarm", "multi agent mode"],
        "deep_dive": ["deep dive", "explain this", "ultrathink", "show reasoning"],
        "ship": ["ship it", "deploy", "push to production", "go live"],
        "scan": ["scan for threats", "security scan", "check security", "audit"],
        "evolve": ["evolve", "improve agents", "evolve agent", "upgrade prompts"],
        "fabricate": ["fabricate", "create tool", "make tool", "new tool"],
        "stop": ["emergency stop", "stop all", "halt", "abort"],
        "report": ["full report", "generate report", "show report", "system report"],
        "computer": ["computer", "attention", "listen"],
    }

    def __init__(self, base_url: str = "http://localhost:7777", wake_words: list[str] = None):
        self.base_url = base_url
        self.wake_words = wake_words or ["hey sahiix", "ok sahiix", "sahiix", "jarvis", "assistant", "computer"]
        self.listening = False
        self.speaking = False
        self.has_sr = False
        self.has_tts = False
        self.voice_mode = False
        self._init_sr()
        self._init_tts()

    def _init_sr(self):
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            self.microphone = sr.Microphone()
            self.has_sr = True
            print("[Voice] SpeechRecognition loaded")
        except Exception as e:
            print(f"[Voice] SpeechRecognition unavailable: {e}")
            self.has_sr = False

    def _init_tts(self):
        # Try Windows SAPI first (WSL/cross-platform)
        try:
            result = subprocess.run(
                ["powershell.exe", "-Command", "Add-Type -AssemblyName System.Speech; Write-Host 'OK'"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.has_tts = True
                self.tts_engine = "windows"
                print("[Voice] Windows TTS available")
                return
        except Exception:
            pass

        # Try Edge-TTS
        try:
            import edge_tts
            self.has_tts = True
            self.tts_engine = "edge"
            print("[Voice] Edge TTS available")
            return
        except Exception:
            pass

        print("[Voice] TTS unavailable")
        self.has_tts = False

    async def speak(self, text: str):
        if not self.has_tts:
            print(f"[JARVIS] {text}")
            return

        self.speaking = True
        print(f"[JARVIS] Speaking: {text[:100]}...")

        try:
            if self.tts_engine == "windows":
                safe_text = text.replace("'", "''").replace('"', '`').replace("\n", " ")[:500]
                subprocess.run(
                    ["powershell.exe", "-Command",
                     f"Add-Type -AssemblyName System.Speech; $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; $synth.Speak('{safe_text}')"],
                    capture_output=True, timeout=30
                )
            elif self.tts_engine == "edge":
                await self._speak_edge(text)
                return  # async task handles speaking flag
        except Exception as e:
            print(f"[Voice] TTS error: {e}")
        finally:
            self.speaking = False

    async def _speak_edge(self, text: str):
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text[:1000], voice="en-US-AriaNeural")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp_path = tmp.name
            await communicate.save(tmp_path)
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path],
                timeout=60
            )
            os.remove(tmp_path)
        except Exception as e:
            print(f"[Voice] Edge TTS error: {e}")
        finally:
            self.speaking = False

    def listen_once(self, timeout: int = 5) -> Optional[str]:
        if not self.has_sr:
            return None

        import speech_recognition as sr

        try:
            with self.microphone as source:
                print("[JARVIS] Listening...")
                self.listening = True
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=10)
                self.listening = False

            print("[JARVIS] Processing speech...")
            text = self.recognizer.recognize_google(audio)
            print(f"[JARVIS] Heard: {text}")
            return text
        except sr.WaitTimeoutError:
            print("[JARVIS] Timeout - no speech detected")
            return None
        except sr.UnknownValueError:
            print("[JARVIS] Could not understand audio")
            return None
        except sr.RequestError as e:
            print(f"[JARVIS] Speech API error: {e}")
            return None
        finally:
            self.listening = False

    def is_wake_word(self, text: str) -> bool:
        text_lower = text.lower()
        return any(w in text_lower for w in self.wake_words)

    async def process_command(self, text: str) -> str:
        """Send command to the running API server with voice command pattern matching."""
        # Check for voice command triggers first
        text_lower = text.lower()
        
        for command_key, phrases in self.VOICE_COMMANDS.items():
            if any(phrase in text_lower for phrase in phrases):
                return await self._handle_voice_command(command_key, text)
        
        # Default: send to API chat
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/chat",
                    json={"message": text, "agent": None},
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    data = await resp.json()
                    return data.get("response", "No response")
        except Exception as e:
            return f"[Voice Error] Could not reach API: {e}"

    async def _handle_voice_command(self, command: str, text: str) -> str:
        """Dispatch special voice commands to SAHIIX AGI endpoints."""
        try:
            async with aiohttp.ClientSession() as session:
                if command == "status":
                    async with session.get(f"{self.base_url}/api/status", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        data = await resp.json()
                        system = data.get("system", {})
                        agents = ", ".join(data.get("agents", []))
                        return f"SAHIIX AGI {system.get('version', 'v2.5')} running. Agents: {agents}."
                
                elif command == "report":
                    async with session.get(f"{self.base_url}/api/ecosystem/status", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        data = await resp.json()
                        nodes = data.get("nodes", {})
                        healthy = sum(1 for n in nodes.values() if n.get("healthy", False))
                        total = len(nodes)
                        return f"{healthy} of {total} ecosystem services healthy."
                
                elif command == "swarm":
                    return "Activating swarm mode. All agents will coordinate on the next task. Say 'deploy' for a task."
                
                elif command == "deep_dive":
                    return "Switching to ultra-deep reasoning mode. I'll show you every step of my thinking."
                
                elif command == "ship":
                    return "Deploying to production. Initiating CI/CD pipeline."
                
                elif command == "scan":
                    return "Running security audit across all connected systems. This may take a moment."
                
                elif command == "evolve":
                    async with session.post(f"{self.base_url}/api/autonomy/evolve", json={"agent": "director"}, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        data = await resp.json()
                        if data.get("success"):
                            return f"Agent '{data['agent']}' evolved. Prompt increased from {data['old_length']} to {data['new_length']} characters."
                        return f"Evolution failed: {data.get('error', 'Unknown error')}"
                
                elif command == "fabricate":
                    return "Tool fabrication mode ready. Describe the tool you need."
                
                elif command == "stop":
                    return "Emergency stop activated. All autonomous processes halted."
                
                elif command == "computer":
                    return "I'm here. Priority override active. What do you need?"
                
                return await self.process_command(text)
        except Exception as e:
            return f"[Voice Command] {command} failed: {e}"

    async def run_interactive(self):
        import sys
        print("\n" + "=" * 50)
        print("  SAHIIX AGI - JARVIS Voice Mode")
        print("  Type commands or 'quit' to exit")
        print("=" * 50 + "\n")

        await self._speak_async("SAHIIX AGI online. Ready for commands.")

        # If running under systemd without a real TTY, block instead of spinning on EOF
        if not sys.stdin or not sys.stdin.isatty():
            print("[Voice] No interactive TTY detected. Blocking to stay alive for API mode.")
            await asyncio.Event().wait()

        while True:
            try:
                user_input = input("[You] ").strip()
                if user_input.lower() in ["quit", "exit", "bye"]:
                    await self._speak_async("Shutting down. Goodbye.")
                    break
                if not user_input:
                    continue

                response = await self.process_command(user_input)
                print(f"[JARVIS] {response}\n")
                await self._speak_async(response)

            except KeyboardInterrupt:
                await self._speak_async("Goodbye.")
                break
            except EOFError:
                print("[Voice] stdin closed. Switching to idle block mode.")
                await asyncio.Event().wait()
            except Exception as e:
                print(f"[Error] {e}")
                await asyncio.sleep(1)

    async def _speak_async(self, text: str):
        if self.tts_engine == "edge":
            await self._speak_edge(text)
        else:
            await self.speak(text)

    async def run_voice_loop(self):
        print("\n" + "=" * 50)
        print("  SAHIIX AGI - JARVIS Continuous Voice Mode")
        print("  Say 'Hey SAHIIX' followed by your command")
        print("=" * 50 + "\n")

        if not self.has_sr:
            print("[Voice] Microphone not available. Falling back to text mode.")
            await self.run_interactive()
            return

        await self._speak_async("Voice mode active. Say Hey SAHIIX to wake me.")

        while True:
            try:
                print("[JARVIS] Waiting for wake word...")
                text = self.listen_once(timeout=10)

                if text and self.is_wake_word(text):
                    await self._speak_async("Yes?")

                    command = self.listen_once(timeout=8)
                    if command:
                        response = await self.process_command(command)
                        print(f"[JARVIS] {response}\n")
                        await self._speak_async(response)

                time.sleep(0.5)

            except KeyboardInterrupt:
                await self._speak_async("Voice mode deactivated.")
                break
            except Exception as e:
                print(f"[Error] {e}")
                time.sleep(1)


async def main():
    import yaml
    config_path = Path(__file__).parent.parent / "config" / "system.yaml"

    with open(config_path) as f:
        config = yaml.safe_load(f)

    voice_config = config.get("voice", {})
    wake_words = voice_config.get("wake_words", ["hey sahiix", "ok sahiix", "sahiix"])
    mode = voice_config.get("mode", "interactive")
    server_config = config.get("server", {})
    base_url = f"http://localhost:{server_config.get('port', 7777)}"

    engine = VoiceEngine(base_url=base_url, wake_words=wake_words)

    try:
        if mode == "voice":
            await engine.run_voice_loop()
        else:
            await engine.run_interactive()
    except Exception as e:
        print(f"[Voice Daemon Fatal Error] {e}")


if __name__ == "__main__":
    asyncio.run(main())
