"""
Chunk handling for P2P file sharing system.

Handles splitting files into chunks, saving/loading chunks to disk,
and reassembling chunks back into the original file.

ANDROID COMPATIBILITY:
- Uses only standard library (os, pathlib)
- All paths are passed in, not hardcoded
- Memory-efficient: streams data, doesn't load entire files
- Kotlin equivalent: FileInputStream/FileOutputStream with BufferedIO

WIRE PROTOCOL NOTE:
Chunk metadata format:
{
    "chunk_index": int,     # Zero-based chunk index
    "hash": str,            # SHA-256 hex digest (64 chars)
    "size": int            # Chunk size in bytes
}
"""

import os
from pathlib import Path
from typing import List, Dict, Optional, Iterator, Callable
from dataclasses import dataclass

# Import from sibling module - handles both package and direct execution
try:
    from utils.hashing import compute_chunk_hash, verify_chunk
    from utils.config import Config, default_config
    from utils.logger import get_logger
except ImportError:
    # Direct execution support
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.hashing import compute_chunk_hash, verify_chunk
    from utils.config import Config, default_config
    from utils.logger import get_logger

logger = get_logger("peer.chunk_handler")


@dataclass
class ChunkInfo:
    """
    Information about a single chunk.
    
    Used for both in-memory operations and serialization.
    When transmitting, 'data' is sent as base64 in the JSON payload.
    """
    chunk_index: int
    data: bytes
    hash: str
    size: int
    
    def to_dict(self, include_data: bool = False) -> Dict:
        """
        Convert to dictionary for JSON serialization.
        
        Args:
            include_data: If True, includes base64-encoded data
            
        Returns:
            Dictionary suitable for JSON serialization
        """
        import base64
        result = {
            "chunk_index": self.chunk_index,
            "hash": self.hash,
            "size": self.size
        }
        if include_data:
            result["data"] = base64.b64encode(self.data).decode('ascii')
        return result
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ChunkInfo':
        """
        Create ChunkInfo from dictionary (received from network).
        
        Args:
            data: Dictionary with chunk_index, hash, size, and optionally data
            
        Returns:
            ChunkInfo instance
        """
        import base64
        chunk_data = b""
        if "data" in data:
            chunk_data = base64.b64decode(data["data"])
        return cls(
            chunk_index=data["chunk_index"],
            data=chunk_data,
            hash=data["hash"],
            size=data["size"]
        )


def split_file(
    filepath: str,
    chunk_size: Optional[int] = None,
    config: Optional[Config] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> List[ChunkInfo]:
    """
    Split a file into fixed-size chunks.
    
    Reads the file in chunks and computes hash for each chunk.
    Memory-efficient: only one chunk is in memory at a time.
    
    Args:
        filepath: Path to the file to split
        chunk_size: Size of each chunk in bytes (default from config)
        config: Configuration object (optional)
        progress_callback: Optional callback(current_chunk, total_chunks) for progress updates
        
    Returns:
        List of ChunkInfo objects with chunk data, hashes, and metadata
        
    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file can't be read
        
    Example:
        chunks = split_file("largefile.zip")
        for chunk in chunks:
            print(f"Chunk {chunk.chunk_index}: {chunk.size} bytes, hash={chunk.hash[:8]}...")
    
    ANDROID NOTE:
    For large files, consider using split_file_streaming() instead to avoid
    holding all chunk data in memory simultaneously.
    """
    cfg = config or default_config
    chunk_sz = chunk_size or cfg.chunk_size
    
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    
    file_size = filepath.stat().st_size
    total_chunks = (file_size + chunk_sz - 1) // chunk_sz  # Ceiling division
    
    logger.info("Splitting file: %s (%d bytes, %d chunks)", filepath.name, file_size, total_chunks)
    
    chunks: List[ChunkInfo] = []
    
    with open(filepath, 'rb') as f:
        chunk_index = 0
        while True:
            data = f.read(chunk_sz)
            if not data:
                break
            
            chunk_hash = compute_chunk_hash(data)
            chunk_info = ChunkInfo(
                chunk_index=chunk_index,
                data=data,
                hash=chunk_hash,
                size=len(data)
            )
            chunks.append(chunk_info)
            
            logger.debug("Chunk %d: %d bytes, hash=%s", chunk_index, len(data), chunk_hash[:8])
            
            if progress_callback:
                progress_callback(chunk_index + 1, total_chunks)
            
            chunk_index += 1
    
    logger.info("Split complete: %d chunks created", len(chunks))
    return chunks


def split_file_streaming(
    filepath: str,
    chunk_size: Optional[int] = None,
    config: Optional[Config] = None
) -> Iterator[ChunkInfo]:
    """
    Generator version of split_file for memory efficiency.
    
    Yields chunks one at a time instead of loading all into memory.
    Ideal for very large files or memory-constrained environments (Android).
    
    Args:
        filepath: Path to the file to split
        chunk_size: Size of each chunk in bytes
        config: Configuration object
        
    Yields:
        ChunkInfo objects one at a time
        
    Example:
        for chunk in split_file_streaming("huge_file.zip"):
            save_chunk(chunk.chunk_index, chunk.data, chunks_dir)
            # chunk can be garbage collected before next iteration
    """
    cfg = config or default_config
    chunk_sz = chunk_size or cfg.chunk_size
    
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    
    with open(filepath, 'rb') as f:
        chunk_index = 0
        while True:
            data = f.read(chunk_sz)
            if not data:
                break
            
            chunk_hash = compute_chunk_hash(data)
            yield ChunkInfo(
                chunk_index=chunk_index,
                data=data,
                hash=chunk_hash,
                size=len(data)
            )
            chunk_index += 1


def save_chunk(
    chunk_index: int,
    data: bytes,
    chunks_dir: str,
    file_id: Optional[str] = None
) -> str:
    """
    Save a chunk to disk.
    
    Chunks are stored as raw binary files with naming convention:
    chunks_dir/[file_id/]chunk_NNNNNN.dat
    
    Args:
        chunk_index: Zero-based chunk index
        data: Raw chunk bytes
        chunks_dir: Directory to save chunks in
        file_id: Optional file ID for subdirectory organization
        
    Returns:
        Full path to the saved chunk file
        
    Example:
        path = save_chunk(0, chunk_data, "./chunks", "abc123")
        # Returns: "./chunks/abc123/chunk_000000.dat"
    """
    # Create directory structure
    if file_id:
        chunk_path = Path(chunks_dir) / file_id
    else:
        chunk_path = Path(chunks_dir)
    
    chunk_path.mkdir(parents=True, exist_ok=True)
    
    # Chunk filename with zero-padded index (supports up to 999,999 chunks)
    filename = f"chunk_{chunk_index:06d}.dat"
    full_path = chunk_path / filename
    
    with open(full_path, 'wb') as f:
        f.write(data)
    
    logger.debug("Saved chunk %d to %s", chunk_index, full_path)
    return str(full_path)


def load_chunk(
    chunk_index: int,
    chunks_dir: str,
    file_id: Optional[str] = None
) -> bytes:
    """
    Load a chunk from disk.
    
    Args:
        chunk_index: Zero-based chunk index
        chunks_dir: Directory where chunks are stored
        file_id: Optional file ID subdirectory
        
    Returns:
        Raw chunk bytes
        
    Raises:
        FileNotFoundError: If chunk file doesn't exist
        IOError: If chunk file can't be read
    """
    if file_id:
        chunk_path = Path(chunks_dir) / file_id
    else:
        chunk_path = Path(chunks_dir)
    
    filename = f"chunk_{chunk_index:06d}.dat"
    full_path = chunk_path / filename
    
    if not full_path.exists():
        raise FileNotFoundError(f"Chunk not found: {full_path}")
    
    with open(full_path, 'rb') as f:
        data = f.read()
    
    logger.debug("Loaded chunk %d from %s (%d bytes)", chunk_index, full_path, len(data))
    return data


def reassemble_file(
    chunks_dir: str,
    output_path: str,
    chunk_hashes: List[str],
    file_id: Optional[str] = None,
    verify: bool = True,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> bool:
    """
    Reassemble chunks back into the original file.
    
    Reads chunks in order and writes them to the output file.
    Optionally verifies each chunk hash before writing.
    
    Args:
        chunks_dir: Directory containing chunk files
        output_path: Path for the reassembled file
        chunk_hashes: List of expected SHA-256 hashes in chunk order
        file_id: Optional file ID subdirectory
        verify: If True, verify each chunk hash before writing
        progress_callback: Optional callback(current_chunk, total_chunks)
        
    Returns:
        True if reassembly succeeded and all hashes verified
        
    Raises:
        FileNotFoundError: If any chunk is missing
        ValueError: If chunk hash verification fails (when verify=True)
        
    Example:
        hashes = ["abc123...", "def456...", ...]
        success = reassemble_file("./chunks", "output.zip", hashes, file_id="xyz")
    """
    total_chunks = len(chunk_hashes)
    logger.info("Reassembling file: %d chunks -> %s", total_chunks, output_path)
    
    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'wb') as out_file:
        for chunk_index, expected_hash in enumerate(chunk_hashes):
            # Load chunk
            try:
                chunk_data = load_chunk(chunk_index, chunks_dir, file_id)
            except FileNotFoundError:
                logger.error("Missing chunk %d during reassembly", chunk_index)
                raise
            
            # Verify hash if requested
            if verify:
                if not verify_chunk(chunk_data, expected_hash):
                    logger.error(
                        "Chunk %d hash mismatch: expected %s, got %s",
                        chunk_index,
                        expected_hash[:8],
                        compute_chunk_hash(chunk_data)[:8]
                    )
                    raise ValueError(f"Chunk {chunk_index} hash verification failed")
            
            # Write to output file
            out_file.write(chunk_data)
            
            if progress_callback:
                progress_callback(chunk_index + 1, total_chunks)
            
            logger.debug("Wrote chunk %d (%d bytes)", chunk_index, len(chunk_data))
    
    logger.info("Reassembly complete: %s", output_path)
    return True


def cleanup_chunks(
    chunks_dir: str,
    file_id: str
) -> int:
    """
    Delete all chunks for a specific file after successful reassembly.
    
    Args:
        chunks_dir: Base chunks directory
        file_id: File ID whose chunks should be deleted
        
    Returns:
        Number of chunk files deleted
        
    Example:
        deleted = cleanup_chunks("./chunks", "abc123")
        print(f"Cleaned up {deleted} chunk files")
    """
    chunk_path = Path(chunks_dir) / file_id
    
    if not chunk_path.exists():
        logger.debug("Chunk directory doesn't exist: %s", chunk_path)
        return 0
    
    deleted_count = 0
    for chunk_file in chunk_path.glob("chunk_*.dat"):
        chunk_file.unlink()
        deleted_count += 1
    
    # Try to remove the empty directory
    try:
        chunk_path.rmdir()
        logger.debug("Removed empty chunk directory: %s", chunk_path)
    except OSError:
        # Directory not empty (other files present) - that's okay
        pass
    
    logger.info("Cleaned up %d chunks for file %s", deleted_count, file_id[:8])
    return deleted_count


def get_chunk_count(file_size: int, chunk_size: Optional[int] = None, config: Optional[Config] = None) -> int:
    """
    Calculate the number of chunks needed for a file of given size.
    
    Args:
        file_size: Total file size in bytes
        chunk_size: Chunk size in bytes (optional)
        config: Configuration object (optional)
        
    Returns:
        Number of chunks (ceiling division)
    """
    cfg = config or default_config
    chunk_sz = chunk_size or cfg.chunk_size
    return (file_size + chunk_sz - 1) // chunk_sz


def list_available_chunks(
    chunks_dir: str,
    file_id: str
) -> List[int]:
    """
    List which chunks are available on disk for a file.
    
    Args:
        chunks_dir: Base chunks directory
        file_id: File ID to check
        
    Returns:
        Sorted list of available chunk indices
        
    Example:
        available = list_available_chunks("./chunks", "abc123")
        # Returns [0, 1, 2, 5, 6] - chunks 3 and 4 are missing
    """
    chunk_path = Path(chunks_dir) / file_id
    
    if not chunk_path.exists():
        return []
    
    indices = []
    for chunk_file in chunk_path.glob("chunk_*.dat"):
        # Extract index from filename: chunk_000005.dat -> 5
        try:
            index_str = chunk_file.stem.split('_')[1]
            indices.append(int(index_str))
        except (IndexError, ValueError):
            logger.warning("Unexpected chunk filename: %s", chunk_file)
    
    return sorted(indices)


# =============================================================================
# Testing Support
# =============================================================================

if __name__ == "__main__":
    import tempfile
    import shutil
    
    print("Chunk Handler Tests")
    print("=" * 60)
    
    # Create a test file
    test_dir = Path("./test_chunks_temp")
    test_dir.mkdir(exist_ok=True)
    
    test_file = test_dir / "test_input.txt"
    test_content = b"Hello, P2P World! " * 1000  # ~18KB
    
    with open(test_file, 'wb') as f:
        f.write(test_content)
    
    print(f"Created test file: {len(test_content)} bytes")
    
    try:
        # Test split_file with small chunks
        chunks = split_file(str(test_file), chunk_size=1024)
        print(f"✓ Split into {len(chunks)} chunks")
        
        # Save chunks
        file_id = "test_file_123"
        for chunk in chunks:
            save_chunk(chunk.chunk_index, chunk.data, str(test_dir / "chunks"), file_id)
        print(f"✓ Saved all chunks to disk")
        
        # List available chunks
        available = list_available_chunks(str(test_dir / "chunks"), file_id)
        print(f"✓ Available chunks: {available}")
        
        # Reassemble file
        output_file = test_dir / "test_output.txt"
        chunk_hashes = [c.hash for c in chunks]
        success = reassemble_file(
            str(test_dir / "chunks"),
            str(output_file),
            chunk_hashes,
            file_id=file_id
        )
        print(f"✓ Reassembly successful: {success}")
        
        # Verify content matches
        with open(output_file, 'rb') as f:
            output_content = f.read()
        
        assert output_content == test_content, "Content mismatch!"
        print(f"✓ Output matches input ({len(output_content)} bytes)")
        
        # Test cleanup
        deleted = cleanup_chunks(str(test_dir / "chunks"), file_id)
        print(f"✓ Cleaned up {deleted} chunk files")
        
        # Test streaming split
        chunk_count = 0
        for chunk in split_file_streaming(str(test_file), chunk_size=1024):
            chunk_count += 1
        print(f"✓ Streaming split yielded {chunk_count} chunks")
        
        print()
        print("All tests passed! ✓")
        
    finally:
        # Cleanup test directory
        shutil.rmtree(test_dir, ignore_errors=True)
        print("Cleaned up test directory")
