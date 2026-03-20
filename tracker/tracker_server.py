"""
TCP tracker server using asyncio.

Listens for peer registrations and queries.
Maintains peer-to-chunk mappings via TrackerStore.

WIRE PROTOCOL:
All messages are newline-delimited JSON.

Request format:
    {"cmd": "COMMAND_NAME", ...params}

Response format:
    {"status": "ok"|"error", ...payload}

Commands:
    REGISTER_FILE - Register file metadata
    REGISTER - Register peer as having a chunk
    REGISTER_BATCH - Register peer as having multiple chunks
    GET_PEERS - Get peers for a file
    UNREGISTER - Unregister peer from a file
    PING - Health check

ANDROID COMPATIBILITY:
- Uses only asyncio (no threading for networking)
- Stateless design allows restart without data loss
- Can be deployed to VPS for internet-accessible tracker

KOTLIN EQUIVALENT:
Use Ktor's TCP server with kotlinx.serialization for JSON.
"""

import asyncio
import json
import time
from typing import Optional, Dict, Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config, default_config, MessageType
from utils.logger import get_logger, setup_logging
from tracker.tracker_store import TrackerStore, get_store

logger = get_logger("tracker.server")


class TrackerProtocol:
    """
    Protocol handler for tracker client connections.
    
    Parses JSON commands and dispatches to appropriate handlers.
    """
    
    def __init__(self, store: TrackerStore):
        self.store = store
    
    async def handle_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Route a command to the appropriate handler.
        
        Args:
            data: Parsed JSON command
            
        Returns:
            Response dictionary
        """
        cmd = data.get("cmd", "").upper()
        
        handlers = {
            MessageType.REGISTER_FILE: self._handle_register_file,
            MessageType.REGISTER: self._handle_register,
            "REGISTER_BATCH": self._handle_register_batch,
            MessageType.GET_PEERS: self._handle_get_peers,
            MessageType.UNREGISTER: self._handle_unregister,
            MessageType.PING: self._handle_ping,
            "STATS": self._handle_stats,
        }
        
        handler = handlers.get(cmd)
        if handler is None:
            return {
                "status": MessageType.STATUS_ERROR,
                "error": f"Unknown command: {cmd}"
            }
        
        try:
            return await handler(data)
        except Exception as e:
            logger.exception("Error handling command %s", cmd)
            return {
                "status": MessageType.STATUS_ERROR,
                "error": str(e)
            }
    
    async def _handle_register_file(self, data: Dict) -> Dict:
        """
        Handle REGISTER_FILE command.
        
        Request:
            {
                "cmd": "REGISTER_FILE",
                "file_id": "abc123...",
                "filename": "movie.mp4",
                "total_chunks": 50,
                "chunk_hashes": ["hash0", "hash1", ...],
                "file_size": 52428800  // optional
            }
        
        Response:
            {"status": "ok", "registered": true|false}
        """
        file_id = data.get("file_id")
        filename = data.get("filename")
        total_chunks = data.get("total_chunks")
        chunk_hashes = data.get("chunk_hashes", [])
        file_size = data.get("file_size", 0)
        
        if not all([file_id, filename, total_chunks]):
            return {
                "status": MessageType.STATUS_ERROR,
                "error": "Missing required fields: file_id, filename, total_chunks"
            }
        
        if len(chunk_hashes) != total_chunks:
            return {
                "status": MessageType.STATUS_ERROR,
                "error": f"chunk_hashes length ({len(chunk_hashes)}) != total_chunks ({total_chunks})"
            }
        
        registered = self.store.register_file_metadata(
            file_id=file_id,
            filename=filename,
            total_chunks=total_chunks,
            chunk_hashes=chunk_hashes,
            file_size=file_size
        )
        
        return {
            "status": MessageType.STATUS_OK,
            "registered": registered
        }
    
    async def _handle_register(self, data: Dict) -> Dict:
        """
        Handle REGISTER command (single chunk).
        
        Request:
            {
                "cmd": "REGISTER",
                "file_id": "abc123...",
                "chunk_index": 0,
                "peer_host": "192.168.1.10",
                "peer_port": 8001
            }
        
        Response:
            {"status": "ok", "registered": true|false}
        """
        file_id = data.get("file_id")
        chunk_index = data.get("chunk_index")
        peer_host = data.get("peer_host")
        peer_port = data.get("peer_port")
        
        if not all([file_id, chunk_index is not None, peer_host, peer_port]):
            return {
                "status": MessageType.STATUS_ERROR,
                "error": "Missing required fields"
            }
        
        registered = self.store.register_peer(
            file_id=file_id,
            chunk_index=chunk_index,
            peer_address=(peer_host, peer_port)
        )
        
        return {
            "status": MessageType.STATUS_OK,
            "registered": registered
        }
    
    async def _handle_register_batch(self, data: Dict) -> Dict:
        """
        Handle REGISTER_BATCH command (multiple chunks).
        
        Request:
            {
                "cmd": "REGISTER_BATCH",
                "file_id": "abc123...",
                "chunk_indices": [0, 1, 2, 3, ...],
                "peer_host": "192.168.1.10",
                "peer_port": 8001
            }
        
        Response:
            {"status": "ok", "registered_count": N}
        """
        file_id = data.get("file_id")
        chunk_indices = data.get("chunk_indices", [])
        peer_host = data.get("peer_host")
        peer_port = data.get("peer_port")
        
        if not all([file_id, chunk_indices, peer_host, peer_port]):
            return {
                "status": MessageType.STATUS_ERROR,
                "error": "Missing required fields"
            }
        
        count = self.store.register_peer_chunks(
            file_id=file_id,
            chunk_indices=chunk_indices,
            peer_address=(peer_host, peer_port)
        )
        
        return {
            "status": MessageType.STATUS_OK,
            "registered_count": count
        }
    
    async def _handle_get_peers(self, data: Dict) -> Dict:
        """
        Handle GET_PEERS command.
        
        Request:
            {"cmd": "GET_PEERS", "file_id": "abc123..."}
        
        Response:
            {
                "status": "ok",
                "file_id": "abc123...",
                "metadata": {
                    "filename": "movie.mp4",
                    "total_chunks": 50,
                    "chunk_hashes": [...],
                    "file_size": 52428800
                },
                "peers": {
                    "0": [["192.168.1.10", 8001], ["192.168.1.11", 8001]],
                    "1": [["192.168.1.10", 8001]],
                    ...
                }
            }
        """
        file_id = data.get("file_id")
        
        if not file_id:
            return {
                "status": MessageType.STATUS_ERROR,
                "error": "Missing file_id"
            }
        
        # Get metadata
        metadata = self.store.get_file_metadata(file_id)
        if metadata is None:
            return {
                "status": MessageType.STATUS_NOT_FOUND,
                "error": f"File not found: {file_id[:16]}..."
            }
        
        # Get peers
        peers = self.store.get_peers(file_id)
        
        # Convert to JSON-serializable format
        # chunk_index (int) -> list of [host, port] pairs
        peers_json = {
            str(idx): [list(addr) for addr in addrs]
            for idx, addrs in peers.items()
        }
        
        return {
            "status": MessageType.STATUS_OK,
            "file_id": file_id,
            "metadata": metadata.to_dict(),
            "peers": peers_json
        }
    
    async def _handle_unregister(self, data: Dict) -> Dict:
        """
        Handle UNREGISTER command.
        
        Request:
            {
                "cmd": "UNREGISTER",
                "file_id": "abc123...",
                "peer_host": "192.168.1.10",
                "peer_port": 8001
            }
        
        Response:
            {"status": "ok", "removed_count": N}
        """
        file_id = data.get("file_id")
        peer_host = data.get("peer_host")
        peer_port = data.get("peer_port")
        
        if not all([file_id, peer_host, peer_port]):
            return {
                "status": MessageType.STATUS_ERROR,
                "error": "Missing required fields"
            }
        
        removed = self.store.unregister_peer(
            file_id=file_id,
            peer_address=(peer_host, peer_port)
        )
        
        return {
            "status": MessageType.STATUS_OK,
            "removed_count": removed
        }
    
    async def _handle_ping(self, data: Dict) -> Dict:
        """Handle PING command for health checks."""
        return {
            "status": MessageType.STATUS_OK,
            "timestamp": time.time(),
            "message": "pong"
        }
    
    async def _handle_stats(self, data: Dict) -> Dict:
        """Handle STATS command for monitoring."""
        return {
            "status": MessageType.STATUS_OK,
            "stats": self.store.get_stats()
        }


class TrackerServer:
    """
    Asyncio TCP server for the tracker.
    
    Usage:
        server = TrackerServer(config)
        await server.start()
        # Server runs until stopped
        await server.stop()
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        store: Optional[TrackerStore] = None
    ):
        self.config = config or default_config
        self.store = store or get_store()
        self.protocol = TrackerProtocol(self.store)
        self._server: Optional[asyncio.Server] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self) -> None:
        """Start the tracker server."""
        host, port = self.config.tracker_host, self.config.tracker_port
        
        self._server = await asyncio.start_server(
            self._handle_client,
            host=host,
            port=port
        )
        
        self._running = True
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        
        logger.info("Tracker server started on %s:%d", host, port)
        
        # Serve forever
        async with self._server:
            await self._server.serve_forever()
    
    async def stop(self) -> None:
        """Stop the tracker server."""
        self._running = False
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        
        logger.info("Tracker server stopped")
    
    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ) -> None:
        """Handle a client connection."""
        addr = writer.get_extra_info('peername')
        logger.debug("Client connected: %s", addr)
        
        try:
            while True:
                # Read until newline
                line = await reader.readline()
                if not line:
                    break
                
                # Parse JSON
                try:
                    data = json.loads(line.decode('utf-8'))
                except json.JSONDecodeError as e:
                    response = {
                        "status": MessageType.STATUS_ERROR,
                        "error": f"Invalid JSON: {e}"
                    }
                else:
                    # Handle command
                    response = await self.protocol.handle_command(data)
                
                # Send response
                response_bytes = json.dumps(response).encode('utf-8') + b'\n'
                writer.write(response_bytes)
                await writer.drain()
                
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            logger.debug("Client disconnected: %s", addr)
        except Exception as e:
            logger.exception("Error handling client %s: %s", addr, e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.debug("Client connection closed: %s", addr)
    
    async def _periodic_cleanup(self) -> None:
        """Periodically clean up expired peer registrations."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Run every minute
                self.store.cleanup_expired_peers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in periodic cleanup: %s", e)


# =============================================================================
# Client helper for testing
# =============================================================================

class TrackerClient:
    """
    Simple async client for the tracker.
    
    Used by peers to communicate with the tracker.
    """
    
    def __init__(self, config: Optional[Config] = None):
        self.config = config or default_config
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
    
    async def connect(self) -> None:
        """Connect to the tracker server."""
        host, port = self.config.tracker_host, self.config.tracker_port
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=self.config.connection_timeout
        )
        logger.debug("Connected to tracker at %s:%d", host, port)
    
    async def close(self) -> None:
        """Close the connection."""
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            self._reader = None
            self._writer = None
    
    async def _send_command(self, command: Dict) -> Dict:
        """Send a command and receive response."""
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected to tracker")
        
        # Send command
        data = json.dumps(command).encode('utf-8') + b'\n'
        self._writer.write(data)
        await self._writer.drain()
        
        # Read response
        response_line = await asyncio.wait_for(
            self._reader.readline(),
            timeout=self.config.read_timeout
        )
        
        return json.loads(response_line.decode('utf-8'))
    
    async def register_file(
        self,
        file_id: str,
        filename: str,
        total_chunks: int,
        chunk_hashes: list,
        file_size: int = 0
    ) -> Dict:
        """Register file metadata with the tracker."""
        return await self._send_command({
            "cmd": MessageType.REGISTER_FILE,
            "file_id": file_id,
            "filename": filename,
            "total_chunks": total_chunks,
            "chunk_hashes": chunk_hashes,
            "file_size": file_size
        })
    
    async def register_peer(
        self,
        file_id: str,
        chunk_index: int,
        peer_host: str,
        peer_port: int
    ) -> Dict:
        """Register as having a chunk."""
        return await self._send_command({
            "cmd": MessageType.REGISTER,
            "file_id": file_id,
            "chunk_index": chunk_index,
            "peer_host": peer_host,
            "peer_port": peer_port
        })
    
    async def register_peer_batch(
        self,
        file_id: str,
        chunk_indices: list,
        peer_host: str,
        peer_port: int
    ) -> Dict:
        """Register as having multiple chunks."""
        return await self._send_command({
            "cmd": "REGISTER_BATCH",
            "file_id": file_id,
            "chunk_indices": chunk_indices,
            "peer_host": peer_host,
            "peer_port": peer_port
        })
    
    async def get_peers(self, file_id: str) -> Dict:
        """Get peers for a file."""
        return await self._send_command({
            "cmd": MessageType.GET_PEERS,
            "file_id": file_id
        })
    
    async def unregister(
        self,
        file_id: str,
        peer_host: str,
        peer_port: int
    ) -> Dict:
        """Unregister from a file."""
        return await self._send_command({
            "cmd": MessageType.UNREGISTER,
            "file_id": file_id,
            "peer_host": peer_host,
            "peer_port": peer_port
        })
    
    async def ping(self) -> Dict:
        """Health check."""
        return await self._send_command({"cmd": MessageType.PING})


# =============================================================================
# Entry point
# =============================================================================

async def run_tracker(config: Optional[Config] = None) -> None:
    """Run the tracker server (blocking)."""
    server = TrackerServer(config)
    try:
        await server.start()
    except KeyboardInterrupt:
        pass
    finally:
        await server.stop()


def main():
    """CLI entry point for running tracker standalone."""
    import argparse
    import logging
    
    parser = argparse.ArgumentParser(description="P2P Tracker Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)
    
    # Create config
    config = Config(tracker_host=args.host, tracker_port=args.port)
    
    print(f"Starting tracker server on {args.host}:{args.port}")
    print("Press Ctrl+C to stop")
    
    asyncio.run(run_tracker(config))


if __name__ == "__main__":
    main()
