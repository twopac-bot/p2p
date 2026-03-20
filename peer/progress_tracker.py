"""
Download progress tracker with persistence.

Tracks download state and persists to JSON for resume support.
Critical for Android where the OS may kill background processes.

ANDROID COMPATIBILITY:
- JSON persistence for process restart survival
- All paths injected via Config
- No in-memory-only state that would be lost

KOTLIN EQUIVALENT:
kotlinx.serialization with Json for persistence.
Store in app's internal storage directory.
"""

import json
import time
from typing import Optional, Set, List, Dict
from dataclasses import dataclass, field, asdict
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config, default_config
from utils.logger import get_logger

logger = get_logger("peer.progress_tracker")


@dataclass
class DownloadState:
    """
    Persistent state for a file download.
    
    Saved to JSON after each chunk completion.
    Loaded on startup to resume interrupted downloads.
    """
    file_id: str
    filename: str
    total_chunks: int
    chunk_hashes: List[str]
    downloaded_chunks: Set[int] = field(default_factory=set)
    file_size: int = 0
    bytes_downloaded: int = 0
    start_time: float = field(default_factory=time.time)
    last_update_time: float = field(default_factory=time.time)
    
    @property
    def is_complete(self) -> bool:
        """Check if all chunks have been downloaded."""
        return len(self.downloaded_chunks) >= self.total_chunks
    
    @property
    def progress_percent(self) -> float:
        """Get download progress as percentage."""
        if self.total_chunks == 0:
            return 100.0
        return (len(self.downloaded_chunks) / self.total_chunks) * 100
    
    @property
    def missing_chunks(self) -> Set[int]:
        """Get set of chunk indices not yet downloaded."""
        all_chunks = set(range(self.total_chunks))
        return all_chunks - self.downloaded_chunks
    
    @property
    def elapsed_seconds(self) -> float:
        """Total elapsed time since download started."""
        return self.last_update_time - self.start_time
    
    @property
    def speed_bps(self) -> float:
        """Average download speed in bytes per second."""
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return self.bytes_downloaded / elapsed
    
    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dictionary."""
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "total_chunks": self.total_chunks,
            "chunk_hashes": self.chunk_hashes,
            "downloaded_chunks": list(self.downloaded_chunks),
            "file_size": self.file_size,
            "bytes_downloaded": self.bytes_downloaded,
            "start_time": self.start_time,
            "last_update_time": self.last_update_time
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'DownloadState':
        """Create from dictionary (loaded from JSON)."""
        return cls(
            file_id=data["file_id"],
            filename=data["filename"],
            total_chunks=data["total_chunks"],
            chunk_hashes=data.get("chunk_hashes", []),
            downloaded_chunks=set(data.get("downloaded_chunks", [])),
            file_size=data.get("file_size", 0),
            bytes_downloaded=data.get("bytes_downloaded", 0),
            start_time=data.get("start_time", time.time()),
            last_update_time=data.get("last_update_time", time.time())
        )


class ProgressTracker:
    """
    Manages download progress with JSON persistence.
    
    Usage:
        tracker = ProgressTracker(config)
        
        # Start new download
        tracker.start_download(file_id, filename, total_chunks, chunk_hashes)
        
        # Mark chunks as complete
        tracker.mark_chunk_done(file_id, chunk_index, chunk_size)
        
        # Check progress
        state = tracker.get_state(file_id)
        if state.is_complete:
            tracker.clear_progress(file_id)
        
        # Resume interrupted download
        state = tracker.load_progress(file_id)
        missing = state.missing_chunks
    """
    
    def __init__(self, config: Optional[Config] = None):
        self.config = config or default_config
        self._states: Dict[str, DownloadState] = {}
        
        # Ensure progress directory exists
        Path(self.config.progress_dir).mkdir(parents=True, exist_ok=True)
    
    def _get_progress_path(self, file_id: str) -> Path:
        """Get the path to the progress file for a download."""
        return Path(self.config.progress_dir) / f"{file_id}.progress.json"
    
    def start_download(
        self,
        file_id: str,
        filename: str,
        total_chunks: int,
        chunk_hashes: List[str],
        file_size: int = 0
    ) -> DownloadState:
        """
        Initialize tracking for a new download.
        
        Args:
            file_id: Unique file identifier
            filename: Original filename
            total_chunks: Number of chunks in the file
            chunk_hashes: List of expected SHA-256 hashes
            file_size: Total file size in bytes
            
        Returns:
            New DownloadState
        """
        state = DownloadState(
            file_id=file_id,
            filename=filename,
            total_chunks=total_chunks,
            chunk_hashes=chunk_hashes,
            file_size=file_size
        )
        
        self._states[file_id] = state
        self._save_state(file_id)
        
        logger.info(
            "Started tracking download: %s (%s, %d chunks)",
            file_id[:8], filename, total_chunks
        )
        
        return state
    
    def load_progress(self, file_id: str) -> Optional[DownloadState]:
        """
        Load progress from disk for resuming a download.
        
        Args:
            file_id: File identifier
            
        Returns:
            DownloadState if found, None otherwise
        """
        # Check in-memory cache first
        if file_id in self._states:
            return self._states[file_id]
        
        # Try loading from disk
        progress_path = self._get_progress_path(file_id)
        if not progress_path.exists():
            return None
        
        try:
            with open(progress_path, 'r') as f:
                data = json.load(f)
            
            state = DownloadState.from_dict(data)
            self._states[file_id] = state
            
            logger.info(
                "Loaded progress for %s: %d/%d chunks",
                file_id[:8], len(state.downloaded_chunks), state.total_chunks
            )
            
            return state
            
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to load progress for %s: %s", file_id[:8], e)
            return None
    
    def get_state(self, file_id: str) -> Optional[DownloadState]:
        """Get the current state for a download (in-memory or from disk)."""
        return self._states.get(file_id) or self.load_progress(file_id)
    
    def mark_chunk_done(
        self,
        file_id: str,
        chunk_index: int,
        chunk_size: int
    ) -> bool:
        """
        Mark a chunk as successfully downloaded.
        
        Args:
            file_id: File identifier
            chunk_index: Index of completed chunk
            chunk_size: Size of the chunk in bytes
            
        Returns:
            True if this was a new chunk, False if already marked
        """
        state = self._states.get(file_id)
        if not state:
            logger.warning("No state for file %s", file_id[:8])
            return False
        
        if chunk_index in state.downloaded_chunks:
            return False
        
        state.downloaded_chunks.add(chunk_index)
        state.bytes_downloaded += chunk_size
        state.last_update_time = time.time()
        
        # Persist immediately - critical for resume
        self._save_state(file_id)
        
        logger.debug(
            "Chunk %d done for %s (%d/%d)",
            chunk_index, file_id[:8],
            len(state.downloaded_chunks), state.total_chunks
        )
        
        return True
    
    def is_complete(self, file_id: str) -> bool:
        """Check if a download is complete."""
        state = self.get_state(file_id)
        return state.is_complete if state else False
    
    def get_missing_chunks(self, file_id: str) -> Set[int]:
        """Get set of chunks not yet downloaded."""
        state = self.get_state(file_id)
        return state.missing_chunks if state else set()
    
    def get_speed(self, file_id: str) -> float:
        """Get current download speed in bytes per second."""
        state = self.get_state(file_id)
        return state.speed_bps if state else 0.0
    
    def get_progress_dict(self, file_id: str) -> Optional[Dict]:
        """Get progress information as a dictionary."""
        state = self.get_state(file_id)
        if not state:
            return None
        
        return {
            "file_id": file_id,
            "filename": state.filename,
            "total_chunks": state.total_chunks,
            "completed_chunks": len(state.downloaded_chunks),
            "missing_chunks": len(state.missing_chunks),
            "percent": state.progress_percent,
            "bytes_downloaded": state.bytes_downloaded,
            "speed_bps": state.speed_bps,
            "is_complete": state.is_complete,
            "elapsed_seconds": state.elapsed_seconds
        }
    
    def clear_progress(self, file_id: str) -> bool:
        """
        Remove progress tracking for a file.
        
        Call after successful completion and file reassembly.
        
        Args:
            file_id: File identifier
            
        Returns:
            True if progress was cleared
        """
        # Remove from memory
        if file_id in self._states:
            del self._states[file_id]
        
        # Remove from disk
        progress_path = self._get_progress_path(file_id)
        if progress_path.exists():
            progress_path.unlink()
            logger.info("Cleared progress for %s", file_id[:8])
            return True
        
        return False
    
    def list_incomplete_downloads(self) -> List[Dict]:
        """
        List all incomplete downloads.
        
        Scans the progress directory for .progress.json files.
        
        Returns:
            List of progress dictionaries for incomplete downloads
        """
        progress_dir = Path(self.config.progress_dir)
        if not progress_dir.exists():
            return []
        
        incomplete = []
        for progress_file in progress_dir.glob("*.progress.json"):
            file_id = progress_file.stem.replace(".progress", "")
            state = self.load_progress(file_id)
            if state and not state.is_complete:
                incomplete.append(self.get_progress_dict(file_id))
        
        return incomplete
    
    def _save_state(self, file_id: str) -> None:
        """Persist state to disk."""
        state = self._states.get(file_id)
        if not state:
            return
        
        progress_path = self._get_progress_path(file_id)
        
        try:
            with open(progress_path, 'w') as f:
                json.dump(state.to_dict(), f, indent=2)
        except IOError as e:
            logger.error("Failed to save progress for %s: %s", file_id[:8], e)


# =============================================================================
# Testing support
# =============================================================================

if __name__ == "__main__":
    import tempfile
    import shutil
    
    print("ProgressTracker Tests")
    print("=" * 60)
    
    # Create temp directory
    test_dir = Path(tempfile.mkdtemp())
    config = Config(progress_dir=str(test_dir / "progress"))
    
    try:
        tracker = ProgressTracker(config)
        
        # Test start download
        file_id = "test_file_abc123"
        state = tracker.start_download(
            file_id=file_id,
            filename="movie.mp4",
            total_chunks=10,
            chunk_hashes=["hash" + str(i) for i in range(10)],
            file_size=10 * 1024 * 1024
        )
        print(f"✓ Started download: {state.filename}")
        
        # Test mark chunks done
        for i in range(5):
            tracker.mark_chunk_done(file_id, i, 1024 * 1024)
        print(f"✓ Marked 5 chunks done")
        
        # Test get progress
        progress = tracker.get_progress_dict(file_id)
        print(f"✓ Progress: {progress['percent']:.1f}% ({progress['completed_chunks']}/{progress['total_chunks']})")
        
        # Test missing chunks
        missing = tracker.get_missing_chunks(file_id)
        print(f"✓ Missing chunks: {sorted(missing)}")
        
        # Test persistence - create new tracker
        tracker2 = ProgressTracker(config)
        state2 = tracker2.load_progress(file_id)
        print(f"✓ Loaded from disk: {len(state2.downloaded_chunks)} chunks")
        
        # Complete download
        for i in range(5, 10):
            tracker2.mark_chunk_done(file_id, i, 1024 * 1024)
        
        print(f"✓ Is complete: {tracker2.is_complete(file_id)}")
        
        # Clear progress
        tracker2.clear_progress(file_id)
        print("✓ Cleared progress")
        
        print()
        print("All tests passed! ✓")
        
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
