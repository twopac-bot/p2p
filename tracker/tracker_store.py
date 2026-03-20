"""
In-memory data store for the tracker server.

Maintains mappings of:
- file_id → file metadata (filename, total chunks, chunk hashes)
- file_id → chunk_index → list of peer addresses

Thread-safe using threading.Lock (required because asyncio may run
callbacks from different threads in some scenarios, and we want this
to be usable in non-async contexts too).

ANDROID COMPATIBILITY:
- Pure Python, no external dependencies
- Could be replaced with SQLite for persistence if needed
- Stateless design - tracker can restart without critical data loss

WIRE PROTOCOL:
Peers register themselves as having specific chunks.
Other peers query for which peers have which chunks.
"""

import threading
import time
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, field
from collections import defaultdict

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.logger import get_logger

logger = get_logger("tracker.store")


# Type aliases for clarity
PeerAddress = Tuple[str, int]  # (host, port)
ChunkIndex = int
FileId = str


@dataclass
class FileMetadata:
    """
    Metadata about a shared file.
    
    Stored once when a file is first registered.
    Used by downloaders to know what chunks to request.
    """
    file_id: str
    filename: str
    total_chunks: int
    chunk_hashes: List[str]  # SHA-256 hash of each chunk, in order
    file_size: int = 0       # Total file size in bytes (optional)
    registered_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dictionary."""
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "total_chunks": self.total_chunks,
            "chunk_hashes": self.chunk_hashes,
            "file_size": self.file_size,
            "registered_at": self.registered_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'FileMetadata':
        """Create from dictionary."""
        return cls(
            file_id=data["file_id"],
            filename=data["filename"],
            total_chunks=data["total_chunks"],
            chunk_hashes=data["chunk_hashes"],
            file_size=data.get("file_size", 0),
            registered_at=data.get("registered_at", time.time())
        )


@dataclass
class PeerInfo:
    """
    Information about a registered peer.
    
    Includes timestamp for TTL-based expiration.
    """
    host: str
    port: int
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    
    @property
    def address(self) -> PeerAddress:
        return (self.host, self.port)
    
    def to_dict(self) -> Dict:
        return {
            "host": self.host,
            "port": self.port,
            "registered_at": self.registered_at,
            "last_seen": self.last_seen
        }


class TrackerStore:
    """
    Thread-safe in-memory store for tracker data.
    
    Maintains:
    - File metadata (filename, chunk hashes)
    - Peer registrations (which peers have which chunks)
    
    Usage:
        store = TrackerStore()
        
        # Register a new file
        store.register_file_metadata("abc123", "movie.mp4", 50, ["hash1", "hash2", ...])
        
        # Register a peer as having chunks
        store.register_peer("abc123", 0, ("192.168.1.10", 8001))
        store.register_peer("abc123", 1, ("192.168.1.10", 8001))
        
        # Query peers
        peers = store.get_peers("abc123")
        # Returns: {0: [("192.168.1.10", 8001)], 1: [("192.168.1.10", 8001)], ...}
    """
    
    # Time in seconds before a peer registration expires
    PEER_TTL: float = 300.0  # 5 minutes
    
    def __init__(self):
        self._lock = threading.Lock()
        
        # file_id -> FileMetadata
        self._file_metadata: Dict[FileId, FileMetadata] = {}
        
        # file_id -> chunk_index -> set of PeerInfo
        # Using set with PeerInfo keyed by address for deduplication
        self._chunk_peers: Dict[FileId, Dict[ChunkIndex, Dict[PeerAddress, PeerInfo]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        
        logger.info("TrackerStore initialized")
    
    def register_file_metadata(
        self,
        file_id: str,
        filename: str,
        total_chunks: int,
        chunk_hashes: List[str],
        file_size: int = 0
    ) -> bool:
        """
        Register metadata for a new file.
        
        Called when a peer first shares a file.
        
        Args:
            file_id: Unique identifier for the file
            filename: Original filename
            total_chunks: Number of chunks the file is split into
            chunk_hashes: List of SHA-256 hashes for each chunk
            file_size: Total file size in bytes
            
        Returns:
            True if newly registered, False if already existed
        """
        with self._lock:
            if file_id in self._file_metadata:
                logger.debug("File metadata already exists: %s", file_id[:8])
                return False
            
            self._file_metadata[file_id] = FileMetadata(
                file_id=file_id,
                filename=filename,
                total_chunks=total_chunks,
                chunk_hashes=chunk_hashes,
                file_size=file_size
            )
            
            logger.info(
                "Registered file: %s (%s, %d chunks)",
                file_id[:8], filename, total_chunks
            )
            return True
    
    def get_file_metadata(self, file_id: str) -> Optional[FileMetadata]:
        """
        Get metadata for a file.
        
        Args:
            file_id: File identifier
            
        Returns:
            FileMetadata if found, None otherwise
        """
        with self._lock:
            return self._file_metadata.get(file_id)
    
    def register_peer(
        self,
        file_id: str,
        chunk_index: int,
        peer_address: PeerAddress
    ) -> bool:
        """
        Register a peer as having a specific chunk.
        
        Called when a peer announces it has a chunk available.
        Updates the last_seen timestamp if already registered.
        
        Args:
            file_id: File identifier
            chunk_index: Which chunk the peer has
            peer_address: (host, port) tuple
            
        Returns:
            True if newly registered, False if updated existing
        """
        host, port = peer_address
        
        with self._lock:
            chunk_map = self._chunk_peers[file_id]
            peer_map = chunk_map[chunk_index]
            
            if peer_address in peer_map:
                # Update existing registration
                peer_map[peer_address].last_seen = time.time()
                logger.debug(
                    "Updated peer %s:%d for chunk %d of %s",
                    host, port, chunk_index, file_id[:8]
                )
                return False
            else:
                # New registration
                peer_map[peer_address] = PeerInfo(host=host, port=port)
                logger.debug(
                    "Registered peer %s:%d for chunk %d of %s",
                    host, port, chunk_index, file_id[:8]
                )
                return True
    
    def register_peer_chunks(
        self,
        file_id: str,
        chunk_indices: List[int],
        peer_address: PeerAddress
    ) -> int:
        """
        Register a peer as having multiple chunks (batch operation).
        
        More efficient than calling register_peer() multiple times.
        
        Args:
            file_id: File identifier
            chunk_indices: List of chunk indices the peer has
            peer_address: (host, port) tuple
            
        Returns:
            Number of new registrations (vs updates)
        """
        host, port = peer_address
        now = time.time()
        new_count = 0
        
        with self._lock:
            chunk_map = self._chunk_peers[file_id]
            
            for chunk_index in chunk_indices:
                peer_map = chunk_map[chunk_index]
                
                if peer_address in peer_map:
                    peer_map[peer_address].last_seen = now
                else:
                    peer_map[peer_address] = PeerInfo(host=host, port=port)
                    new_count += 1
            
            logger.info(
                "Registered peer %s:%d for %d chunks of %s (%d new)",
                host, port, len(chunk_indices), file_id[:8], new_count
            )
        
        return new_count
    
    def unregister_peer(
        self,
        file_id: str,
        peer_address: PeerAddress
    ) -> int:
        """
        Unregister a peer from all chunks of a file.
        
        Called when a peer gracefully disconnects.
        
        Args:
            file_id: File identifier
            peer_address: (host, port) tuple
            
        Returns:
            Number of chunk registrations removed
        """
        removed = 0
        
        with self._lock:
            if file_id not in self._chunk_peers:
                return 0
            
            chunk_map = self._chunk_peers[file_id]
            for chunk_index in list(chunk_map.keys()):
                if peer_address in chunk_map[chunk_index]:
                    del chunk_map[chunk_index][peer_address]
                    removed += 1
                    
                    # Clean up empty chunk entries
                    if not chunk_map[chunk_index]:
                        del chunk_map[chunk_index]
            
            # Clean up empty file entries
            if not chunk_map:
                del self._chunk_peers[file_id]
        
        if removed:
            logger.info(
                "Unregistered peer %s:%d from %d chunks of %s",
                peer_address[0], peer_address[1], removed, file_id[:8]
            )
        
        return removed
    
    def get_peers(
        self,
        file_id: str,
        exclude_expired: bool = True
    ) -> Dict[ChunkIndex, List[PeerAddress]]:
        """
        Get all peers for each chunk of a file.
        
        Args:
            file_id: File identifier
            exclude_expired: If True, filter out peers past TTL
            
        Returns:
            Dictionary mapping chunk_index to list of peer addresses
            
        Example:
            {
                0: [("192.168.1.10", 8001), ("192.168.1.11", 8001)],
                1: [("192.168.1.10", 8001)],
                ...
            }
        """
        now = time.time()
        result: Dict[ChunkIndex, List[PeerAddress]] = {}
        
        with self._lock:
            if file_id not in self._chunk_peers:
                return result
            
            chunk_map = self._chunk_peers[file_id]
            
            for chunk_index, peer_map in chunk_map.items():
                peers = []
                for addr, info in peer_map.items():
                    if exclude_expired and (now - info.last_seen > self.PEER_TTL):
                        continue
                    peers.append(addr)
                
                if peers:
                    result[chunk_index] = peers
        
        return result
    
    def get_peers_for_chunk(
        self,
        file_id: str,
        chunk_index: int,
        exclude_expired: bool = True
    ) -> List[PeerAddress]:
        """
        Get peers that have a specific chunk.
        
        Args:
            file_id: File identifier
            chunk_index: Which chunk to query
            exclude_expired: If True, filter out peers past TTL
            
        Returns:
            List of peer addresses
        """
        peers = self.get_peers(file_id, exclude_expired)
        return peers.get(chunk_index, [])
    
    def cleanup_expired_peers(self) -> int:
        """
        Remove all expired peer registrations.
        
        Should be called periodically by the tracker server.
        
        Returns:
            Number of peer registrations removed
        """
        now = time.time()
        removed = 0
        
        with self._lock:
            for file_id in list(self._chunk_peers.keys()):
                chunk_map = self._chunk_peers[file_id]
                
                for chunk_index in list(chunk_map.keys()):
                    peer_map = chunk_map[chunk_index]
                    
                    for addr in list(peer_map.keys()):
                        if now - peer_map[addr].last_seen > self.PEER_TTL:
                            del peer_map[addr]
                            removed += 1
                    
                    # Clean up empty chunk entries
                    if not peer_map:
                        del chunk_map[chunk_index]
                
                # Clean up empty file entries
                if not chunk_map:
                    del self._chunk_peers[file_id]
        
        if removed:
            logger.info("Cleaned up %d expired peer registrations", removed)
        
        return removed
    
    def get_stats(self) -> Dict:
        """
        Get statistics about the store.
        
        Returns:
            Dictionary with file count, peer registrations, etc.
        """
        with self._lock:
            total_registrations = sum(
                len(peer_map)
                for chunk_map in self._chunk_peers.values()
                for peer_map in chunk_map.values()
            )
            
            return {
                "files": len(self._file_metadata),
                "files_with_peers": len(self._chunk_peers),
                "total_peer_registrations": total_registrations
            }
    
    def file_exists(self, file_id: str) -> bool:
        """Check if a file is registered."""
        with self._lock:
            return file_id in self._file_metadata
    
    def list_files(self) -> List[str]:
        """List all registered file IDs."""
        with self._lock:
            return list(self._file_metadata.keys())


# =============================================================================
# Singleton instance for simple usage
# =============================================================================

_default_store: Optional[TrackerStore] = None


def get_store() -> TrackerStore:
    """Get the default TrackerStore singleton."""
    global _default_store
    if _default_store is None:
        _default_store = TrackerStore()
    return _default_store


# =============================================================================
# Testing Support
# =============================================================================

if __name__ == "__main__":
    print("TrackerStore Tests")
    print("=" * 60)
    
    store = TrackerStore()
    
    # Test file registration
    file_id = "test_file_abc123"
    hashes = ["hash0", "hash1", "hash2"]
    
    result = store.register_file_metadata(file_id, "movie.mp4", 3, hashes, 1024000)
    print(f"✓ Registered file: {result}")
    
    # Test duplicate registration
    result = store.register_file_metadata(file_id, "movie.mp4", 3, hashes)
    print(f"✓ Duplicate registration returned: {result} (expected False)")
    
    # Test get metadata
    meta = store.get_file_metadata(file_id)
    print(f"✓ Got metadata: {meta.filename}, {meta.total_chunks} chunks")
    
    # Test peer registration
    peer1 = ("192.168.1.10", 8001)
    peer2 = ("192.168.1.11", 8001)
    
    store.register_peer(file_id, 0, peer1)
    store.register_peer(file_id, 1, peer1)
    store.register_peer(file_id, 0, peer2)
    print("✓ Registered peers")
    
    # Test get peers
    peers = store.get_peers(file_id)
    print(f"✓ Got peers: {dict(peers)}")
    
    # Test batch registration
    count = store.register_peer_chunks(file_id, [0, 1, 2], ("192.168.1.12", 8001))
    print(f"✓ Batch registered: {count} new")
    
    # Test unregister
    removed = store.unregister_peer(file_id, peer1)
    print(f"✓ Unregistered peer: {removed} chunks removed")
    
    # Test stats
    stats = store.get_stats()
    print(f"✓ Stats: {stats}")
    
    print()
    print("All tests passed! ✓")
