"""
Cryptographic hashing utilities for P2P file sharing system.

Uses SHA-256 for all hashing operations:
- Chunk integrity verification
- File identification (file_id generation)

ANDROID COMPATIBILITY:
- Uses only hashlib from Python standard library
- No platform-specific crypto libraries
- Kotlin equivalent: java.security.MessageDigest("SHA-256")

WIRE PROTOCOL NOTE:
All hashes are transmitted as lowercase hex strings (64 characters).
"""

import hashlib
from typing import Union


def compute_chunk_hash(data: bytes) -> str:
    """
    Compute SHA-256 hash of a chunk's data.
    
    Args:
        data: Raw bytes of the chunk
        
    Returns:
        Lowercase hexadecimal string (64 characters)
        
    Example:
        >>> compute_chunk_hash(b"hello world")
        'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9'
    """
    return hashlib.sha256(data).hexdigest()


def verify_chunk(data: bytes, expected_hash: str) -> bool:
    """
    Verify that a chunk's data matches its expected hash.
    
    Args:
        data: Raw bytes of the chunk
        expected_hash: Expected SHA-256 hex digest
        
    Returns:
        True if hashes match (case-insensitive comparison)
        
    Example:
        >>> data = b"hello world"
        >>> hash_val = compute_chunk_hash(data)
        >>> verify_chunk(data, hash_val)
        True
        >>> verify_chunk(b"tampered", hash_val)
        False
    """
    actual_hash = compute_chunk_hash(data)
    # Case-insensitive comparison for robustness
    return actual_hash.lower() == expected_hash.lower()


def compute_file_id(filename: str, filesize: int, first_chunk_hash: str) -> str:
    """
    Generate a deterministic file ID from file properties.
    
    The file ID is a SHA-256 hash of:
    - Filename (just the name, not full path)
    - File size in bytes
    - Hash of the first chunk
    
    This ensures the same file always gets the same ID, enabling:
    - Deduplication across peers
    - Resume of interrupted downloads
    - Verification that peers are serving the same file
    
    Args:
        filename: Name of the file (basename only, no path)
        filesize: Total file size in bytes
        first_chunk_hash: SHA-256 hash of the first chunk
        
    Returns:
        Lowercase hexadecimal file ID (64 characters)
        
    Example:
        >>> compute_file_id("movie.mp4", 1073741824, "abc123...")
        'a1b2c3...'  # Deterministic based on inputs
        
    KOTLIN EQUIVALENT:
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update(filename.toByteArray(Charsets.UTF_8))
        digest.update(filesize.toString().toByteArray(Charsets.UTF_8))
        digest.update(firstChunkHash.toByteArray(Charsets.UTF_8))
        return digest.digest().toHexString()
    """
    hasher = hashlib.sha256()
    # Encode each component as UTF-8 bytes
    hasher.update(filename.encode('utf-8'))
    hasher.update(str(filesize).encode('utf-8'))
    hasher.update(first_chunk_hash.encode('utf-8'))
    return hasher.hexdigest()


def compute_file_hash(filepath: str, chunk_size: int = 8192) -> str:
    """
    Compute SHA-256 hash of an entire file by reading in chunks.
    
    This is used for full-file verification after reassembly.
    Reads the file in chunks to avoid loading large files into memory.
    
    Args:
        filepath: Path to the file
        chunk_size: Size of chunks to read (default 8KB)
        
    Returns:
        Lowercase hexadecimal hash of the entire file
        
    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file can't be read
    """
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            hasher.update(data)
    return hasher.hexdigest()


def hash_string(text: str) -> str:
    """
    Compute SHA-256 hash of a string.
    
    Utility function for hashing arbitrary strings (e.g., peer IDs).
    
    Args:
        text: String to hash
        
    Returns:
        Lowercase hexadecimal hash
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def short_hash(full_hash: str, length: int = 8) -> str:
    """
    Get a shortened version of a hash for display purposes.
    
    Args:
        full_hash: Full 64-character hash
        length: Number of characters to return (default 8)
        
    Returns:
        First `length` characters of the hash
        
    Example:
        >>> short_hash("b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")
        'b94d27b9'
    """
    return full_hash[:length]


# =============================================================================
# Testing Support
# =============================================================================

if __name__ == "__main__":
    # Quick verification when run directly
    print("Hashing Module Tests")
    print("=" * 40)
    
    # Test compute_chunk_hash
    test_data = b"Hello, P2P World!"
    hash_result = compute_chunk_hash(test_data)
    print(f"Test data: {test_data}")
    print(f"Hash: {hash_result}")
    print(f"Hash length: {len(hash_result)} (expected 64)")
    
    # Test verify_chunk
    assert verify_chunk(test_data, hash_result), "verify_chunk should return True for matching hash"
    assert not verify_chunk(b"wrong data", hash_result), "verify_chunk should return False for non-matching"
    print("✓ verify_chunk works correctly")
    
    # Test compute_file_id
    file_id = compute_file_id("test.txt", 1024, hash_result)
    print(f"File ID: {file_id}")
    
    # Test determinism - same inputs should produce same output
    file_id_2 = compute_file_id("test.txt", 1024, hash_result)
    assert file_id == file_id_2, "file_id should be deterministic"
    print("✓ compute_file_id is deterministic")
    
    # Test hash_string
    string_hash = hash_string("peer123")
    print(f"String hash: {string_hash}")
    
    # Test short_hash
    short = short_hash(hash_result)
    print(f"Short hash: {short} (first 8 chars)")
    
    print()
    print("All tests passed! ✓")
