"""
Chunk uploader - TCP server that serves chunks to other peers.

Listens for incoming chunk requests and sends chunk data.

WIRE PROTOCOL:
Request (newline-delimited JSON):
    {"cmd": "GET_CHUNK", "file_id": "abc123...", "chunk_index": 0}

Response:
    Success: {"status": "ok", "chunk_index": 0, "data": "<base64>", "hash": "..."}
    Error:   {"status": "error", "error": "Chunk not found"}

ANDROID COMPATIBILITY:
- Uses asyncio, not threading
- All paths injected via Config
- Can run as background service

KOTLIN EQUIVALENT:
Ktor TCP server with base64 encoding via java.util.Base64
"""

import asyncio
import json
import base64
from typing import Optional, Callable, Awaitable

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config, default_config, MessageType
from utils.logger import get_logger
from utils.hashing import compute_chunk_hash
from peer.chunk_handler import load_chunk

logger = get_logger("peer.uploader")


class ChunkUploader:
    """
    TCP server that serves chunks to other peers.
    
    Usage:
        uploader = ChunkUploader(config, file_id)
        await uploader.start()
        # Server runs in background
        await uploader.stop()
    
    The uploader serves chunks for a specific file from the local chunks directory.
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        file_id: Optional[str] = None,
        on_chunk_served: Optional[Callable[[int, str], Awaitable[None]]] = None
    ):
        """
        Initialize the uploader.
        
        Args:
            config: Configuration object
            file_id: If set, only serve chunks for this file. If None, serve any requested file.
            on_chunk_served: Optional async callback(chunk_index, peer_address) when a chunk is served
        """
        self.config = config or default_config
        self.file_id = file_id
        self.on_chunk_served = on_chunk_served
        self._server: Optional[asyncio.Server] = None
        self._running = False
        
        # Statistics
        self.chunks_served = 0
        self.bytes_uploaded = 0
    
    async def start(self, host: str = "0.0.0.0", port: Optional[int] = None) -> int:
        """
        Start the upload server.
        
        Args:
            host: Host to bind to (default: all interfaces)
            port: Port to bind to (default: from config, or 0 for auto-assign)
            
        Returns:
            The actual port the server is listening on
        """
        bind_port = port if port is not None else self.config.peer_port
        
        self._server = await asyncio.start_server(
            self._handle_client,
            host=host,
            port=bind_port
        )
        
        self._running = True
        
        # Get actual port (useful when port=0 for auto-assign)
        actual_port = self._server.sockets[0].getsockname()[1]
        
        logger.info(
            "Uploader started on %s:%d (file: %s)",
            host, actual_port,
            self.file_id[:8] if self.file_id else "any"
        )
        
        return actual_port
    
    async def serve_forever(self) -> None:
        """Serve requests until stopped."""
        if self._server:
            async with self._server:
                await self._server.serve_forever()
    
    async def stop(self) -> None:
        """Stop the upload server."""
        self._running = False
        
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        
        logger.info(
            "Uploader stopped. Served %d chunks, %d bytes",
            self.chunks_served, self.bytes_uploaded
        )
    
    @property
    def is_running(self) -> bool:
        return self._running and self._server is not None
    
    def get_address(self) -> tuple:
        """Get the (host, port) the server is listening on."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()
        return ("", 0)
    
    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ) -> None:
        """Handle a peer connection requesting chunks."""
        addr = writer.get_extra_info('peername')
        logger.debug("Peer connected: %s", addr)
        
        try:
            while True:
                # Read request
                line = await reader.readline()
                if not line:
                    break
                
                # Parse request
                try:
                    request = json.loads(line.decode('utf-8'))
                except json.JSONDecodeError as e:
                    response = {
                        "status": MessageType.STATUS_ERROR,
                        "error": f"Invalid JSON: {e}"
                    }
                else:
                    # Handle request
                    response = await self._handle_request(request, addr)
                
                # Send response
                response_bytes = json.dumps(response).encode('utf-8') + b'\n'
                writer.write(response_bytes)
                await writer.drain()
                
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            logger.debug("Peer disconnected: %s", addr)
        except Exception as e:
            logger.exception("Error handling peer %s: %s", addr, e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
    
    async def _handle_request(self, request: dict, peer_addr: tuple) -> dict:
        """Handle a single chunk request."""
        cmd = request.get("cmd", "").upper()
        
        if cmd == MessageType.GET_CHUNK:
            return await self._handle_get_chunk(request, peer_addr)
        elif cmd == MessageType.PING:
            return {"status": MessageType.STATUS_OK, "message": "pong"}
        else:
            return {
                "status": MessageType.STATUS_ERROR,
                "error": f"Unknown command: {cmd}"
            }
    
    async def _handle_get_chunk(self, request: dict, peer_addr: tuple) -> dict:
        """
        Handle GET_CHUNK request.
        
        Request:
            {"cmd": "GET_CHUNK", "file_id": "...", "chunk_index": 0}
        
        Response:
            {"status": "ok", "chunk_index": 0, "data": "<base64>", "hash": "...", "size": N}
        """
        file_id = request.get("file_id")
        chunk_index = request.get("chunk_index")
        
        if not file_id or chunk_index is None:
            return {
                "status": MessageType.STATUS_ERROR,
                "error": "Missing file_id or chunk_index"
            }
        
        # Check if we're restricted to a specific file
        if self.file_id and file_id != self.file_id:
            return {
                "status": MessageType.STATUS_ERROR,
                "error": "File not available from this peer"
            }
        
        # Load chunk from disk
        try:
            chunk_data = load_chunk(
                chunk_index=chunk_index,
                chunks_dir=self.config.chunks_dir,
                file_id=file_id
            )
        except FileNotFoundError:
            logger.warning(
                "Chunk not found: %s chunk %d",
                file_id[:8], chunk_index
            )
            return {
                "status": MessageType.STATUS_NOT_FOUND,
                "error": f"Chunk {chunk_index} not found"
            }
        except IOError as e:
            logger.error(
                "IO error loading chunk %s/%d: %s",
                file_id[:8], chunk_index, e
            )
            return {
                "status": MessageType.STATUS_ERROR,
                "error": f"IO error: {e}"
            }
        
        # Compute hash and encode data
        chunk_hash = compute_chunk_hash(chunk_data)
        chunk_b64 = base64.b64encode(chunk_data).decode('ascii')
        
        # Update statistics
        self.chunks_served += 1
        self.bytes_uploaded += len(chunk_data)
        
        logger.debug(
            "Served chunk %d of %s to %s (%d bytes)",
            chunk_index, file_id[:8], peer_addr, len(chunk_data)
        )
        
        # Callback
        if self.on_chunk_served:
            try:
                await self.on_chunk_served(chunk_index, f"{peer_addr[0]}:{peer_addr[1]}")
            except Exception as e:
                logger.warning("on_chunk_served callback failed: %s", e)
        
        return {
            "status": MessageType.STATUS_OK,
            "chunk_index": chunk_index,
            "data": chunk_b64,
            "hash": chunk_hash,
            "size": len(chunk_data)
        }
    
    def get_stats(self) -> dict:
        """Get upload statistics."""
        return {
            "chunks_served": self.chunks_served,
            "bytes_uploaded": self.bytes_uploaded,
            "is_running": self.is_running
        }


# =============================================================================
# Standalone server for testing
# =============================================================================

async def run_uploader(config: Config, file_id: str) -> None:
    """Run an uploader server for a specific file."""
    async def on_served(chunk_idx, peer):
        print(f"Served chunk {chunk_idx} to {peer}")
    
    uploader = ChunkUploader(
        config=config,
        file_id=file_id,
        on_chunk_served=on_served
    )
    
    port = await uploader.start()
    print(f"Uploader running on port {port}")
    print("Press Ctrl+C to stop")
    
    try:
        await uploader.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        await uploader.stop()


def main():
    """CLI entry point for testing uploader."""
    import argparse
    import logging
    from utils.logger import setup_logging
    
    parser = argparse.ArgumentParser(description="Chunk Uploader Server")
    parser.add_argument("--file-id", required=True, help="File ID to serve")
    parser.add_argument("--port", type=int, default=8001, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)
    
    config = Config(peer_port=args.port)
    asyncio.run(run_uploader(config, args.file_id))


if __name__ == "__main__":
    main()
