"""
Parallel chunk downloader using asyncio.

Downloads chunks from multiple peers simultaneously with:
- Concurrent connections (limited by semaphore)
- Hash verification
- Retry logic with peer failover
- Progress callbacks for UI integration

ANDROID COMPATIBILITY:
- Uses asyncio, not threading
- Progress events via callback/queue for Android UI
- Resume support via progress_tracker integration

KOTLIN EQUIVALENT:
Kotlin coroutines with Dispatchers.IO for network calls.
Use kotlinx.coroutines.async for parallel downloads.
"""

import asyncio
import json
import base64
import random
import time
from typing import Dict, List, Tuple, Optional, Callable, Awaitable, Set
from dataclasses import dataclass, field
from enum import Enum

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config, default_config, MessageType
from utils.logger import get_logger
from utils.hashing import verify_chunk
from peer.chunk_handler import save_chunk

logger = get_logger("peer.downloader")


# Type aliases
PeerAddress = Tuple[str, int]
ChunkIndex = int


class DownloadStatus(Enum):
    """Status of a chunk download."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ChunkDownloadResult:
    """Result of downloading a single chunk."""
    chunk_index: int
    status: DownloadStatus
    peer: Optional[PeerAddress] = None
    size: int = 0
    attempts: int = 0
    error: Optional[str] = None
    duration: float = 0.0


@dataclass
class DownloadProgress:
    """Progress information for the entire download."""
    file_id: str
    total_chunks: int
    completed_chunks: int
    failed_chunks: int
    bytes_downloaded: int
    start_time: float
    current_chunk: Optional[int] = None
    
    @property
    def percent(self) -> float:
        if self.total_chunks == 0:
            return 100.0
        return (self.completed_chunks / self.total_chunks) * 100
    
    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time
    
    @property
    def speed_bps(self) -> float:
        """Bytes per second."""
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return self.bytes_downloaded / elapsed
    
    @property
    def eta_seconds(self) -> float:
        """Estimated time remaining in seconds."""
        if self.completed_chunks == 0:
            return float('inf')
        
        speed = self.speed_bps
        if speed <= 0:
            return float('inf')
        
        # Estimate remaining bytes based on average chunk size
        avg_chunk_size = self.bytes_downloaded / self.completed_chunks
        remaining_chunks = self.total_chunks - self.completed_chunks
        remaining_bytes = remaining_chunks * avg_chunk_size
        
        return remaining_bytes / speed
    
    def to_dict(self) -> Dict:
        return {
            "file_id": self.file_id,
            "total_chunks": self.total_chunks,
            "completed_chunks": self.completed_chunks,
            "failed_chunks": self.failed_chunks,
            "bytes_downloaded": self.bytes_downloaded,
            "percent": self.percent,
            "speed_bps": self.speed_bps,
            "eta_seconds": self.eta_seconds if self.eta_seconds != float('inf') else None,
            "elapsed_seconds": self.elapsed_seconds
        }


class ChunkDownloader:
    """
    Parallel chunk downloader.
    
    Downloads chunks from multiple peers concurrently.
    
    Usage:
        downloader = ChunkDownloader(config, file_id, chunk_hashes, on_progress=callback)
        
        # chunk_peers: {chunk_index: [(host, port), ...]}
        results = await downloader.download_chunks(chunk_peers)
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        file_id: Optional[str] = None,
        chunk_hashes: Optional[List[str]] = None,
        on_progress: Optional[Callable[[DownloadProgress], Awaitable[None]]] = None,
        on_chunk_complete: Optional[Callable[[ChunkDownloadResult], Awaitable[None]]] = None
    ):
        """
        Initialize the downloader.
        
        Args:
            config: Configuration object
            file_id: ID of the file being downloaded
            chunk_hashes: List of expected SHA-256 hashes for each chunk
            on_progress: Async callback for progress updates
            on_chunk_complete: Async callback when a chunk completes
        """
        self.config = config or default_config
        self.file_id = file_id
        self.chunk_hashes = chunk_hashes or []
        self.on_progress = on_progress
        self.on_chunk_complete = on_chunk_complete
        
        # Download state
        self._progress: Optional[DownloadProgress] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._cancelled = False
    
    async def download_chunks(
        self,
        chunk_peers: Dict[ChunkIndex, List[PeerAddress]],
        skip_chunks: Optional[Set[ChunkIndex]] = None
    ) -> List[ChunkDownloadResult]:
        """
        Download all specified chunks.
        
        Args:
            chunk_peers: Mapping of chunk_index to list of peer addresses
            skip_chunks: Set of chunk indices to skip (already downloaded)
            
        Returns:
            List of ChunkDownloadResult for each chunk
        """
        skip = skip_chunks or set()
        chunks_to_download = [idx for idx in chunk_peers.keys() if idx not in skip]
        
        if not chunks_to_download:
            logger.info("No chunks to download")
            return []
        
        logger.info(
            "Starting download of %d chunks for file %s",
            len(chunks_to_download),
            self.file_id[:8] if self.file_id else "unknown"
        )
        
        # Initialize progress
        self._progress = DownloadProgress(
            file_id=self.file_id or "",
            total_chunks=len(chunk_peers),
            completed_chunks=len(skip),
            failed_chunks=0,
            bytes_downloaded=0,
            start_time=time.time()
        )
        
        # Semaphore to limit concurrent connections
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_downloads)
        self._cancelled = False
        
        # Create download tasks
        tasks = [
            self._download_chunk_with_retry(idx, chunk_peers[idx])
            for idx in chunks_to_download
        ]
        
        # Run all downloads concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        download_results = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Download task failed: %s", result)
                download_results.append(ChunkDownloadResult(
                    chunk_index=-1,
                    status=DownloadStatus.FAILED,
                    error=str(result)
                ))
            else:
                download_results.append(result)
        
        # Final progress update
        if self.on_progress:
            await self.on_progress(self._progress)
        
        success = sum(1 for r in download_results if r.status == DownloadStatus.COMPLETED)
        logger.info(
            "Download complete: %d/%d chunks successful",
            success, len(chunks_to_download)
        )
        
        return download_results
    
    def cancel(self) -> None:
        """Cancel the download."""
        self._cancelled = True
        logger.info("Download cancelled")
    
    async def _download_chunk_with_retry(
        self,
        chunk_index: int,
        peers: List[PeerAddress]
    ) -> ChunkDownloadResult:
        """
        Download a chunk with retry logic.
        
        Tries each peer up to retry_attempts times.
        """
        if not peers:
            return ChunkDownloadResult(
                chunk_index=chunk_index,
                status=DownloadStatus.FAILED,
                error="No peers available"
            )
        
        # Shuffle peers to distribute load
        peers_shuffled = list(peers)
        random.shuffle(peers_shuffled)
        
        attempts = 0
        last_error = None
        
        for attempt in range(self.config.chunk_retry_attempts):
            if self._cancelled:
                return ChunkDownloadResult(
                    chunk_index=chunk_index,
                    status=DownloadStatus.FAILED,
                    error="Download cancelled"
                )
            
            # Pick a peer (round-robin through shuffled list)
            peer = peers_shuffled[attempt % len(peers_shuffled)]
            attempts += 1
            
            async with self._semaphore:
                result = await self._download_chunk_from_peer(chunk_index, peer)
            
            result.attempts = attempts
            
            if result.status == DownloadStatus.COMPLETED:
                # Update progress
                self._progress.completed_chunks += 1
                self._progress.bytes_downloaded += result.size
                
                # Callbacks
                if self.on_chunk_complete:
                    await self.on_chunk_complete(result)
                if self.on_progress:
                    await self.on_progress(self._progress)
                
                return result
            
            last_error = result.error
            logger.warning(
                "Chunk %d attempt %d failed from %s:%d: %s",
                chunk_index, attempt + 1, peer[0], peer[1], last_error
            )
            
            # Brief delay before retry
            await asyncio.sleep(0.5)
        
        # All retries failed
        self._progress.failed_chunks += 1
        
        result = ChunkDownloadResult(
            chunk_index=chunk_index,
            status=DownloadStatus.FAILED,
            attempts=attempts,
            error=f"All {attempts} attempts failed: {last_error}"
        )
        
        if self.on_chunk_complete:
            await self.on_chunk_complete(result)
        
        return result
    
    async def _download_chunk_from_peer(
        self,
        chunk_index: int,
        peer: PeerAddress
    ) -> ChunkDownloadResult:
        """
        Download a single chunk from a specific peer.
        """
        host, port = peer
        start_time = time.time()
        
        try:
            # Connect to peer
            # Buffer must handle base64-encoded 1MB chunks (~1.4MB)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, limit=2 * 1024 * 1024),
                timeout=self.config.connection_timeout
            )
            
            try:
                # Send request
                request = {
                    "cmd": MessageType.GET_CHUNK,
                    "file_id": self.file_id,
                    "chunk_index": chunk_index
                }
                request_bytes = json.dumps(request).encode('utf-8') + b'\n'
                writer.write(request_bytes)
                await writer.drain()
                
                # Read response
                response_line = await asyncio.wait_for(
                    reader.readline(),
                    timeout=self.config.read_timeout
                )
                
                if not response_line:
                    return ChunkDownloadResult(
                        chunk_index=chunk_index,
                        status=DownloadStatus.FAILED,
                        peer=peer,
                        error="Empty response from peer"
                    )
                
                response = json.loads(response_line.decode('utf-8'))
                
            finally:
                writer.close()
                await writer.wait_closed()
            
            # Check response status
            if response.get("status") != MessageType.STATUS_OK:
                return ChunkDownloadResult(
                    chunk_index=chunk_index,
                    status=DownloadStatus.FAILED,
                    peer=peer,
                    error=response.get("error", "Unknown error")
                )
            
            # Decode chunk data
            chunk_b64 = response.get("data", "")
            chunk_data = base64.b64decode(chunk_b64)
            expected_hash = response.get("hash", "")
            
            # Verify hash
            if self.chunk_hashes and chunk_index < len(self.chunk_hashes):
                expected_from_metadata = self.chunk_hashes[chunk_index]
                if not verify_chunk(chunk_data, expected_from_metadata):
                    return ChunkDownloadResult(
                        chunk_index=chunk_index,
                        status=DownloadStatus.FAILED,
                        peer=peer,
                        error=f"Hash mismatch (expected {expected_from_metadata[:8]})"
                    )
            elif expected_hash:
                # Verify against hash from response
                if not verify_chunk(chunk_data, expected_hash):
                    return ChunkDownloadResult(
                        chunk_index=chunk_index,
                        status=DownloadStatus.FAILED,
                        peer=peer,
                        error="Hash mismatch with response hash"
                    )
            
            # Save chunk to disk
            save_chunk(
                chunk_index=chunk_index,
                data=chunk_data,
                chunks_dir=self.config.chunks_dir,
                file_id=self.file_id
            )
            
            duration = time.time() - start_time
            
            logger.debug(
                "Downloaded chunk %d from %s:%d (%d bytes, %.2fs)",
                chunk_index, host, port, len(chunk_data), duration
            )
            
            return ChunkDownloadResult(
                chunk_index=chunk_index,
                status=DownloadStatus.COMPLETED,
                peer=peer,
                size=len(chunk_data),
                duration=duration
            )
            
        except asyncio.TimeoutError:
            return ChunkDownloadResult(
                chunk_index=chunk_index,
                status=DownloadStatus.FAILED,
                peer=peer,
                error="Connection timeout"
            )
        except ConnectionRefusedError:
            return ChunkDownloadResult(
                chunk_index=chunk_index,
                status=DownloadStatus.FAILED,
                peer=peer,
                error="Connection refused"
            )
        except Exception as e:
            logger.exception("Error downloading chunk %d from %s:%d", chunk_index, host, port)
            return ChunkDownloadResult(
                chunk_index=chunk_index,
                status=DownloadStatus.FAILED,
                peer=peer,
                error=str(e)
            )


# =============================================================================
# Testing support
# =============================================================================

if __name__ == "__main__":
    print("ChunkDownloader module")
    print("=" * 40)
    print("This module provides parallel chunk downloading.")
    print("Use it via peer_node.py or the CLI.")
    print()
    print("Classes:")
    print("  - DownloadStatus: Enum for chunk status")
    print("  - ChunkDownloadResult: Result of single chunk download")
    print("  - DownloadProgress: Overall download progress")
    print("  - ChunkDownloader: Main downloader class")
