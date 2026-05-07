import base64
import io
import zipfile
from typing import Any, Dict, Optional

import aiohttp


async def auto_backup_archiver(
    backup_items: Optional[Dict[str, Any]] = None,
    archive_name: str = "backup.zip",
    **kwargs: Any
) -> str:
    """Asynchronously build an in-memory ZIP archive from local or remote sources.
    
    Fetches any HTTP/HTTPS values using aiohttp with a strict 10-second timeout
    and returns the archive as a base64-encoded string.
    
    Args:
        backup_items: Mapping of archive member names to their content.
            String values starting with http:// or https:// are fetched.
            Bytes and other values are serialized directly.
        archive_name: Identifier name for the archive.
        **kwargs: Flexible keyword arguments for SAHIIX AGI integration.
        
    Returns:
        A string containing either the base64-encoded archive data or an
        error message prefixed with ERROR.
    """
    try:
        items = backup_items if backup_items is not None else {}
        buffer = io.BytesIO()
        timeout = aiohttp.ClientTimeout(total=10)

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for name, content in items.items():
                    if isinstance(content, str) and content.startswith(("http://", "https://")):
                        async with session.get(content) as resp:
                            resp.raise_for_status()
                            data = await resp.read()
                            zf.writestr(name, data)
                    elif isinstance(content, bytes):
                        zf.writestr(name, content)
                    else:
                        zf.writestr(name, str(content).encode("utf-8"))

        buffer.seek(0)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return encoded
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"