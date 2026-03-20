"""
Configuration module for P2P file sharing system.

All configuration values can be overridden via environment variables,
making this Android-compatible where the app layer injects paths at runtime.

ANDROID COMPATIBILITY:
- All paths default to relative paths (no /tmp, /var, etc.)
- Config class allows runtime injection for mobile environments
- Environment variable overrides supported for containerized deployments
"""

import os
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Default Constants
# =============================================================================

# Chunk size: 1MB - balance between transfer efficiency and memory usage
# Smaller chunks = more parallelism but more overhead
# Larger chunks = less overhead but less parallelism
DEFAULT_CHUNK_SIZE: int = 1 * 1024 * 1024  # 1 MB

# Tracker server defaults
DEFAULT_TRACKER_HOST: str = "127.0.0.1"
DEFAULT_TRACKER_PORT: int = 8000

# Peer server defaults
DEFAULT_PEER_PORT: int = 8001

# Directory paths - Android will override these
DEFAULT_DOWNLOAD_DIR: str = "./downloads"
DEFAULT_CHUNKS_DIR: str = "./chunks"
DEFAULT_PROGRESS_DIR: str = "./progress"
DEFAULT_UPLOADS_DIR: str = "./uploads"

# Network settings
DEFAULT_MAX_CONCURRENT_DOWNLOADS: int = 5
DEFAULT_CHUNK_RETRY_ATTEMPTS: int = 3
DEFAULT_CONNECTION_TIMEOUT: float = 30.0
DEFAULT_READ_TIMEOUT: float = 60.0

# Protocol version for future compatibility
PROTOCOL_VERSION: str = "1.0"


# =============================================================================
# Environment Variable Loading
# =============================================================================

def _get_env_int(key: str, default: int) -> int:
    """Get integer from environment variable with fallback to default."""
    value = os.environ.get(key)
    if value is not None:
        try:
            return int(value)
        except ValueError:
            pass
    return default


def _get_env_float(key: str, default: float) -> float:
    """Get float from environment variable with fallback to default."""
    value = os.environ.get(key)
    if value is not None:
        try:
            return float(value)
        except ValueError:
            pass
    return default


def _get_env_str(key: str, default: str) -> str:
    """Get string from environment variable with fallback to default."""
    return os.environ.get(key, default)


# =============================================================================
# Configuration Class
# =============================================================================

@dataclass
class Config:
    """
    Central configuration container for the P2P system.
    
    All settings can be overridden at instantiation time, allowing the Android
    layer to inject appropriate paths and settings at runtime.
    
    Usage:
        # Default configuration
        config = Config()
        
        # Custom configuration for Android
        config = Config(
            download_dir="/data/data/com.app/files/downloads",
            chunks_dir="/data/data/com.app/files/chunks",
            tracker_host="tracker.example.com"
        )
    """
    
    # Chunk settings
    chunk_size: int = field(default_factory=lambda: _get_env_int("P2P_CHUNK_SIZE", DEFAULT_CHUNK_SIZE))
    
    # Tracker settings
    tracker_host: str = field(default_factory=lambda: _get_env_str("P2P_TRACKER_HOST", DEFAULT_TRACKER_HOST))
    tracker_port: int = field(default_factory=lambda: _get_env_int("P2P_TRACKER_PORT", DEFAULT_TRACKER_PORT))
    
    # Peer settings
    peer_port: int = field(default_factory=lambda: _get_env_int("P2P_PEER_PORT", DEFAULT_PEER_PORT))
    
    # Directory paths - Android will override these
    download_dir: str = field(default_factory=lambda: _get_env_str("P2P_DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR))
    chunks_dir: str = field(default_factory=lambda: _get_env_str("P2P_CHUNKS_DIR", DEFAULT_CHUNKS_DIR))
    progress_dir: str = field(default_factory=lambda: _get_env_str("P2P_PROGRESS_DIR", DEFAULT_PROGRESS_DIR))
    uploads_dir: str = field(default_factory=lambda: _get_env_str("P2P_UPLOADS_DIR", DEFAULT_UPLOADS_DIR))
    
    # Network tuning
    max_concurrent_downloads: int = field(default_factory=lambda: _get_env_int("P2P_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT_DOWNLOADS))
    chunk_retry_attempts: int = field(default_factory=lambda: _get_env_int("P2P_RETRY_ATTEMPTS", DEFAULT_CHUNK_RETRY_ATTEMPTS))
    connection_timeout: float = field(default_factory=lambda: _get_env_float("P2P_CONN_TIMEOUT", DEFAULT_CONNECTION_TIMEOUT))
    read_timeout: float = field(default_factory=lambda: _get_env_float("P2P_READ_TIMEOUT", DEFAULT_READ_TIMEOUT))
    
    # Protocol
    protocol_version: str = PROTOCOL_VERSION
    
    def ensure_directories(self) -> None:
        """
        Create all required directories if they don't exist.
        
        Call this during application startup. On Android, this should be called
        after the Config is initialized with the correct app-specific paths.
        """
        for directory in [self.download_dir, self.chunks_dir, self.progress_dir, self.uploads_dir]:
            os.makedirs(directory, exist_ok=True)
    
    def get_chunk_path(self, file_id: str, chunk_index: int) -> str:
        """Get the filesystem path for a specific chunk."""
        # Chunks are organized by file_id to support multiple concurrent transfers
        chunk_dir = os.path.join(self.chunks_dir, file_id)
        os.makedirs(chunk_dir, exist_ok=True)
        return os.path.join(chunk_dir, f"chunk_{chunk_index:06d}.dat")
    
    def get_progress_path(self, file_id: str) -> str:
        """Get the filesystem path for the progress file of a download."""
        os.makedirs(self.progress_dir, exist_ok=True)
        return os.path.join(self.progress_dir, f"{file_id}.progress.json")
    
    def get_tracker_address(self) -> tuple[str, int]:
        """Get tracker address as a tuple for socket connections."""
        return (self.tracker_host, self.tracker_port)
    
    def get_peer_address(self, host: Optional[str] = None) -> tuple[str, int]:
        """Get peer address as a tuple. Host defaults to 0.0.0.0 for binding."""
        return (host or "0.0.0.0", self.peer_port)


# =============================================================================
# Global Default Instance
# =============================================================================

# Default config instance for convenience
# Production code should create its own Config instance for testability
default_config = Config()


# =============================================================================
# Wire Protocol Message Types (for documentation and validation)
# =============================================================================

class MessageType:
    """
    Protocol message types for tracker and peer communication.
    
    These are documented here for clarity and to ensure protocol compatibility
    when reimplementing in Kotlin for Android.
    
    WIRE FORMAT:
    All messages are JSON objects terminated by newline (\\n).
    All messages contain a "cmd" field indicating the message type.
    """
    
    # Tracker commands (sent TO tracker)
    REGISTER = "REGISTER"           # Register a peer as having a chunk
    REGISTER_FILE = "REGISTER_FILE" # Register file metadata (name, hashes, total chunks)
    GET_PEERS = "GET_PEERS"         # Request peer list for a file
    UNREGISTER = "UNREGISTER"       # Unregister a peer (graceful shutdown)
    
    # Peer commands (sent TO peer)
    GET_CHUNK = "GET_CHUNK"         # Request a specific chunk
    PING = "PING"                   # Health check
    
    # Response status values
    STATUS_OK = "ok"
    STATUS_ERROR = "error"
    STATUS_NOT_FOUND = "not_found"


# =============================================================================
# Testing Support
# =============================================================================

def _run_demo():
    """Demo function to show configuration values."""
    print("P2P File Share Configuration")
    print("=" * 40)
    
    config = Config()
    print(f"Chunk size: {config.chunk_size / 1024 / 1024:.1f} MB")
    print(f"Tracker: {config.tracker_host}:{config.tracker_port}")
    print(f"Peer port: {config.peer_port}")
    print(f"Download dir: {config.download_dir}")
    print(f"Chunks dir: {config.chunks_dir}")
    print(f"Progress dir: {config.progress_dir}")
    print(f"Max concurrent: {config.max_concurrent_downloads}")
    print(f"Protocol version: {config.protocol_version}")
    print()
    print("Environment variable overrides:")
    print("  P2P_CHUNK_SIZE, P2P_TRACKER_HOST, P2P_TRACKER_PORT")
    print("  P2P_PEER_PORT, P2P_DOWNLOAD_DIR, P2P_CHUNKS_DIR")
    print("  P2P_PROGRESS_DIR, P2P_MAX_CONCURRENT, P2P_RETRY_ATTEMPTS")
    print("  P2P_CONN_TIMEOUT, P2P_READ_TIMEOUT")


if __name__ == "__main__":
    _run_demo()
