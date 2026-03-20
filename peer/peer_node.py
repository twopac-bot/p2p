"""
Main peer node - orchestrates upload and download operations.

This is the core engine that CLI and REST API both use.
Designed as a pure engine with no UI dependencies.

ANDROID COMPATIBILITY:
- No print() or input() - uses callbacks and return values
- All paths from Config (injectable)
- Asyncio-based for event loop compatibility
- Progress callbacks for UI updates

KOTLIN EQUIVALENT:
Kotlin class with suspend functions for async operations.
Same public API, different implementation.
"""

import asyncio
import os
from typing import Optional, Callable, Awaitable, Dict, List, Set
from dataclasses import dataclass
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config, default_config
from utils.logger import get_logger
from utils.hashing import compute_file_id
from peer.chunk_handler import (
    split_file,
    save_chunk,
    reassemble_file,
    cleanup_chunks,
    list_available_chunks
)
from peer.uploader import ChunkUploader
from peer.downloader import ChunkDownloader, DownloadProgress, DownloadStatus
from peer.progress_tracker import ProgressTracker
from tracker.tracker_server import TrackerClient

logger = get_logger("peer.node")


@dataclass
class UploadResult:
    """Result of an upload operation."""
    success: bool
    file_id: Optional[str] = None
    filename: Optional[str] = None
    total_chunks: int = 0
    file_size: int = 0
    peer_port: int = 0
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "file_id": self.file_id,
            "filename": self.filename,
            "total_chunks": self.total_chunks,
            "file_size": self.file_size,
            "peer_port": self.peer_port,
            "error": self.error
        }


@dataclass 
class DownloadResult:
    """Result of a download operation."""
    success: bool
    file_id: Optional[str] = None
    filename: Optional[str] = None
    output_path: Optional[str] = None
    total_chunks: int = 0
    downloaded_chunks: int = 0
    failed_chunks: int = 0
    bytes_downloaded: int = 0
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "file_id": self.file_id,
            "filename": self.filename,
            "output_path": self.output_path,
            "total_chunks": self.total_chunks,
            "downloaded_chunks": self.downloaded_chunks,
            "failed_chunks": self.failed_chunks,
            "bytes_downloaded": self.bytes_downloaded,
            "error": self.error
        }


class PeerNode:
    """
    Main peer node that handles uploading and downloading files.
    
    This is the core engine used by both CLI and REST API.
    No UI code here - only business logic.
    
    Usage:
        node = PeerNode(config)
        
        # Upload a file
        result = await node.upload("/path/to/file.zip")
        print(f"File ID: {result.file_id}")
        
        # Download a file
        result = await node.download("abc123...", on_progress=my_callback)
        print(f"Downloaded to: {result.output_path}")
    """
    
    def __init__(self, config: Optional[Config] = None):
        self.config = config or default_config
        self.progress_tracker = ProgressTracker(self.config)
        
        # Active uploaders (file_id -> ChunkUploader)
        self._uploaders: Dict[str, ChunkUploader] = {}
        
        # Ensure directories exist
        self.config.ensure_directories()
    
    async def upload(
        self,
        filepath: str,
        peer_host: Optional[str] = None,
        peer_port: Optional[int] = None,
        on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None
    ) -> UploadResult:
        """
        Upload a file to the P2P network.
        
        Steps:
        1. Split file into chunks
        2. Generate deterministic file ID
        3. Save chunks to local storage
        4. Register file metadata with tracker
        5. Register chunks with tracker
        6. Start uploader server
        
        Args:
            filepath: Path to the file to share
            peer_host: Host to advertise to tracker (default: auto-detect)
            peer_port: Port for uploader server (default: from config)
            on_progress: Async callback(current_chunk, total_chunks)
            
        Returns:
            UploadResult with file_id and status
        """
        filepath = Path(filepath)
        
        if not filepath.exists():
            return UploadResult(
                success=False,
                error=f"File not found: {filepath}"
            )
        
        filename = filepath.name
        file_size = filepath.stat().st_size
        
        logger.info("Starting upload: %s (%d bytes)", filename, file_size)
        
        try:
            # Step 1: Split file into chunks
            logger.info("Splitting file into chunks...")
            
            async def progress_wrapper(current: int, total: int) -> None:
                if on_progress:
                    await on_progress(current, total)
            
            # Run sync split_file in thread pool to not block event loop
            loop = asyncio.get_event_loop()
            chunks = await loop.run_in_executor(
                None,
                lambda: split_file(str(filepath), config=self.config)
            )
            
            if not chunks:
                return UploadResult(
                    success=False,
                    error="Failed to split file - no chunks created"
                )
            
            # Step 2: Generate file ID
            first_chunk_hash = chunks[0].hash
            file_id = compute_file_id(filename, file_size, first_chunk_hash)
            
            logger.info("File ID: %s", file_id[:16])
            
            # Step 3: Save chunks to local storage
            logger.info("Saving %d chunks to disk...", len(chunks))
            chunk_hashes = []
            
            for chunk in chunks:
                save_chunk(
                    chunk_index=chunk.chunk_index,
                    data=chunk.data,
                    chunks_dir=self.config.chunks_dir,
                    file_id=file_id
                )
                chunk_hashes.append(chunk.hash)
            
            # Step 4: Register with tracker
            logger.info("Registering with tracker...")
            
            tracker = TrackerClient(self.config)
            await tracker.connect()
            
            try:
                # Register file metadata
                response = await tracker.register_file(
                    file_id=file_id,
                    filename=filename,
                    total_chunks=len(chunks),
                    chunk_hashes=chunk_hashes,
                    file_size=file_size
                )
                
                if response.get("status") != "ok":
                    return UploadResult(
                        success=False,
                        error=f"Tracker registration failed: {response.get('error')}"
                    )
                
                # Step 5: Register as peer for all chunks
                # Determine our address
                host = peer_host or self.config.tracker_host  # Use tracker host as hint
                port = peer_port or self.config.peer_port
                
                response = await tracker.register_peer_batch(
                    file_id=file_id,
                    chunk_indices=list(range(len(chunks))),
                    peer_host=host,
                    peer_port=port
                )
                
            finally:
                await tracker.close()
            
            # Step 6: Start uploader server
            logger.info("Starting uploader server...")
            
            uploader = ChunkUploader(
                config=self.config,
                file_id=file_id
            )
            
            actual_port = await uploader.start(port=port)
            self._uploaders[file_id] = uploader
            
            # Start serving in background
            asyncio.create_task(uploader.serve_forever())
            
            logger.info(
                "Upload complete. File ID: %s, Port: %d",
                file_id[:16], actual_port
            )
            
            return UploadResult(
                success=True,
                file_id=file_id,
                filename=filename,
                total_chunks=len(chunks),
                file_size=file_size,
                peer_port=actual_port
            )
            
        except Exception as e:
            logger.exception("Upload failed: %s", e)
            return UploadResult(
                success=False,
                filename=filename,
                error=str(e)
            )
    
    async def download(
        self,
        file_id: str,
        output_dir: Optional[str] = None,
        on_progress: Optional[Callable[[DownloadProgress], Awaitable[None]]] = None
    ) -> DownloadResult:
        """
        Download a file from the P2P network.
        
        Steps:
        1. Load existing progress (resume support)
        2. Fetch metadata and peer list from tracker
        3. Identify missing chunks
        4. Download chunks in parallel
        5. Reassemble file
        6. Clean up chunks
        
        Args:
            file_id: ID of the file to download
            output_dir: Directory for output file (default: from config)
            on_progress: Async callback for progress updates
            
        Returns:
            DownloadResult with output path and status
        """
        logger.info("Starting download: %s", file_id[:16])
        
        try:
            # Step 1: Check for existing progress (resume)
            existing_state = self.progress_tracker.load_progress(file_id)
            downloaded_chunks: Set[int] = set()
            
            if existing_state:
                downloaded_chunks = existing_state.downloaded_chunks
                logger.info(
                    "Resuming download: %d/%d chunks already done",
                    len(downloaded_chunks), existing_state.total_chunks
                )
            
            # Step 2: Fetch from tracker
            logger.info("Fetching peer list from tracker...")
            
            tracker = TrackerClient(self.config)
            await tracker.connect()
            
            try:
                response = await tracker.get_peers(file_id)
            finally:
                await tracker.close()
            
            if response.get("status") != "ok":
                return DownloadResult(
                    success=False,
                    file_id=file_id,
                    error=f"Tracker error: {response.get('error')}"
                )
            
            metadata = response.get("metadata", {})
            peers_data = response.get("peers", {})
            
            filename = metadata.get("filename", "unknown")
            total_chunks = metadata.get("total_chunks", 0)
            chunk_hashes = metadata.get("chunk_hashes", [])
            file_size = metadata.get("file_size", 0)
            
            # Convert peers data to proper format
            # peers_data: {"0": [["host", port], ...], "1": [...], ...}
            chunk_peers: Dict[int, List[tuple]] = {}
            for idx_str, peer_list in peers_data.items():
                idx = int(idx_str)
                chunk_peers[idx] = [tuple(p) for p in peer_list]
            
            if not chunk_peers:
                return DownloadResult(
                    success=False,
                    file_id=file_id,
                    filename=filename,
                    error="No peers available"
                )
            
            # Step 3: Initialize or update progress tracker
            if not existing_state:
                self.progress_tracker.start_download(
                    file_id=file_id,
                    filename=filename,
                    total_chunks=total_chunks,
                    chunk_hashes=chunk_hashes,
                    file_size=file_size
                )
            
            # Also check which chunks we have on disk
            available = set(list_available_chunks(self.config.chunks_dir, file_id))
            downloaded_chunks = downloaded_chunks.union(available)
            
            # Mark already-available chunks as done in progress tracker
            for chunk_idx in available:
                if chunk_idx not in (existing_state.downloaded_chunks if existing_state else set()):
                    # Estimate chunk size (we don't know exact size without reading)
                    self.progress_tracker.mark_chunk_done(file_id, chunk_idx, self.config.chunk_size)
            
            missing_chunks = set(range(total_chunks)) - downloaded_chunks
            
            if not missing_chunks:
                logger.info("All chunks already downloaded")
            else:
                # Step 4: Download missing chunks
                logger.info("Downloading %d missing chunks...", len(missing_chunks))
                
                # Progress callback wrapper
                async def downloader_progress(progress: DownloadProgress) -> None:
                    if on_progress:
                        await on_progress(progress)
                
                async def on_chunk_done(result) -> None:
                    if result.status == DownloadStatus.COMPLETED:
                        self.progress_tracker.mark_chunk_done(
                            file_id, result.chunk_index, result.size
                        )
                
                downloader = ChunkDownloader(
                    config=self.config,
                    file_id=file_id,
                    chunk_hashes=chunk_hashes,
                    on_progress=downloader_progress,
                    on_chunk_complete=on_chunk_done
                )
                
                # Filter chunk_peers to only missing chunks
                missing_peers = {
                    idx: chunk_peers.get(idx, [])
                    for idx in missing_chunks
                    if idx in chunk_peers
                }
                
                results = await downloader.download_chunks(
                    missing_peers,
                    skip_chunks=downloaded_chunks
                )
                
                # Count results
                completed = sum(1 for r in results if r.status == DownloadStatus.COMPLETED)
                failed = sum(1 for r in results if r.status == DownloadStatus.FAILED)
                
                if failed > 0:
                    logger.warning("%d chunks failed to download", failed)
            
            # Step 5: Check if complete and reassemble
            final_state = self.progress_tracker.get_state(file_id)
            
            if not final_state or not final_state.is_complete:
                missing = self.progress_tracker.get_missing_chunks(file_id)
                return DownloadResult(
                    success=False,
                    file_id=file_id,
                    filename=filename,
                    total_chunks=total_chunks,
                    downloaded_chunks=total_chunks - len(missing),
                    failed_chunks=len(missing),
                    error=f"Download incomplete: {len(missing)} chunks missing"
                )
            
            # Reassemble file
            logger.info("Reassembling file...")
            
            out_dir = Path(output_dir or self.config.download_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            output_path = out_dir / filename
            
            # Handle filename conflicts
            if output_path.exists():
                stem = output_path.stem
                suffix = output_path.suffix
                counter = 1
                while output_path.exists():
                    output_path = out_dir / f"{stem}_{counter}{suffix}"
                    counter += 1
            
            success = reassemble_file(
                chunks_dir=self.config.chunks_dir,
                output_path=str(output_path),
                chunk_hashes=chunk_hashes,
                file_id=file_id,
                verify=True
            )
            
            if not success:
                return DownloadResult(
                    success=False,
                    file_id=file_id,
                    filename=filename,
                    error="File reassembly failed"
                )
            
            # Step 6: Clean up
            cleanup_chunks(self.config.chunks_dir, file_id)
            self.progress_tracker.clear_progress(file_id)
            
            logger.info("Download complete: %s", output_path)
            
            return DownloadResult(
                success=True,
                file_id=file_id,
                filename=filename,
                output_path=str(output_path),
                total_chunks=total_chunks,
                downloaded_chunks=total_chunks,
                bytes_downloaded=final_state.bytes_downloaded
            )
            
        except Exception as e:
            logger.exception("Download failed: %s", e)
            return DownloadResult(
                success=False,
                file_id=file_id,
                error=str(e)
            )
    
    async def seed(
        self,
        file_id: str,
        peer_host: Optional[str] = None,
        peer_port: Optional[int] = None
    ) -> bool:
        """
        Start seeding a file that was previously uploaded.
        
        Use this to resume seeding after a restart.
        
        Args:
            file_id: ID of the file to seed
            peer_host: Host to advertise
            peer_port: Port for uploader server
            
        Returns:
            True if seeding started successfully
        """
        # Check if we have chunks for this file
        available = list_available_chunks(self.config.chunks_dir, file_id)
        if not available:
            logger.error("No chunks found for file %s", file_id[:8])
            return False
        
        logger.info("Starting to seed file %s (%d chunks)", file_id[:8], len(available))
        
        # Register with tracker
        tracker = TrackerClient(self.config)
        await tracker.connect()
        
        try:
            host = peer_host or self.config.tracker_host
            port = peer_port or self.config.peer_port
            
            response = await tracker.register_peer_batch(
                file_id=file_id,
                chunk_indices=available,
                peer_host=host,
                peer_port=port
            )
            
            if response.get("status") != "ok":
                logger.error("Failed to register with tracker")
                return False
                
        finally:
            await tracker.close()
        
        # Start uploader
        uploader = ChunkUploader(
            config=self.config,
            file_id=file_id
        )
        
        actual_port = await uploader.start(port=peer_port or self.config.peer_port)
        self._uploaders[file_id] = uploader
        
        asyncio.create_task(uploader.serve_forever())
        
        logger.info("Seeding on port %d", actual_port)
        return True
    
    async def get_status(self, file_id: str) -> Optional[Dict]:
        """Get download status for a file."""
        return self.progress_tracker.get_progress_dict(file_id)
    
    async def list_incomplete(self) -> List[Dict]:
        """List all incomplete downloads."""
        return self.progress_tracker.list_incomplete_downloads()
    
    async def stop_upload(self, file_id: str) -> bool:
        """Stop uploading a file."""
        if file_id in self._uploaders:
            await self._uploaders[file_id].stop()
            del self._uploaders[file_id]
            return True
        return False
    
    async def stop_all(self) -> None:
        """Stop all upload servers."""
        for file_id in list(self._uploaders.keys()):
            await self.stop_upload(file_id)


# =============================================================================
# Testing support
# =============================================================================

if __name__ == "__main__":
    print("PeerNode - Core P2P Engine")
    print("=" * 40)
    print()
    print("This module provides the main PeerNode class.")
    print("Use via CLI (cli/main.py) or REST API (api/rest_api.py)")
    print()
    print("Public methods:")
    print("  - upload(filepath) -> UploadResult")
    print("  - download(file_id) -> DownloadResult")
    print("  - seed(file_id) -> bool")
    print("  - get_status(file_id) -> dict")
    print("  - list_incomplete() -> list")
    print("  - stop_upload(file_id) -> bool")
    print("  - stop_all() -> None")
