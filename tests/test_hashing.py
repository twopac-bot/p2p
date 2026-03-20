"""
Unit tests for the hashing module.

Run with: python -m pytest tests/test_hashing.py -v
Or directly: python tests/test_hashing.py
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.hashing import (
    compute_chunk_hash,
    verify_chunk,
    compute_file_id,
    compute_file_hash,
    hash_string,
    short_hash
)


class TestComputeChunkHash:
    """Tests for compute_chunk_hash function."""
    
    def test_empty_bytes(self):
        """Empty bytes should produce consistent hash."""
        result = compute_chunk_hash(b"")
        # SHA-256 of empty string is well-known
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert result == expected
    
    def test_simple_data(self):
        """Simple data should produce correct hash."""
        result = compute_chunk_hash(b"hello")
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert result == expected
    
    def test_returns_lowercase_hex(self):
        """Hash should always be lowercase hex."""
        result = compute_chunk_hash(b"test data")
        assert result == result.lower()
        assert all(c in '0123456789abcdef' for c in result)
    
    def test_returns_64_chars(self):
        """SHA-256 hash should always be 64 hex characters."""
        result = compute_chunk_hash(b"any data here")
        assert len(result) == 64
    
    def test_deterministic(self):
        """Same input should always produce same output."""
        data = b"deterministic test"
        result1 = compute_chunk_hash(data)
        result2 = compute_chunk_hash(data)
        assert result1 == result2
    
    def test_different_data_different_hash(self):
        """Different data should produce different hashes."""
        hash1 = compute_chunk_hash(b"data one")
        hash2 = compute_chunk_hash(b"data two")
        assert hash1 != hash2
    
    def test_binary_data(self):
        """Should handle binary data correctly."""
        binary_data = bytes(range(256))  # All byte values 0-255
        result = compute_chunk_hash(binary_data)
        assert len(result) == 64


class TestVerifyChunk:
    """Tests for verify_chunk function."""
    
    def test_valid_hash_returns_true(self):
        """Valid hash should return True."""
        data = b"verify me"
        hash_val = compute_chunk_hash(data)
        assert verify_chunk(data, hash_val) is True
    
    def test_invalid_hash_returns_false(self):
        """Invalid hash should return False."""
        data = b"original data"
        wrong_hash = compute_chunk_hash(b"different data")
        assert verify_chunk(data, wrong_hash) is False
    
    def test_case_insensitive(self):
        """Hash comparison should be case-insensitive."""
        data = b"case test"
        hash_lower = compute_chunk_hash(data)
        hash_upper = hash_lower.upper()
        assert verify_chunk(data, hash_upper) is True
    
    def test_empty_data(self):
        """Empty data should verify correctly."""
        data = b""
        hash_val = compute_chunk_hash(data)
        assert verify_chunk(data, hash_val) is True


class TestComputeFileId:
    """Tests for compute_file_id function."""
    
    def test_deterministic(self):
        """Same inputs should produce same file ID."""
        id1 = compute_file_id("test.txt", 1024, "abc123")
        id2 = compute_file_id("test.txt", 1024, "abc123")
        assert id1 == id2
    
    def test_different_filename_different_id(self):
        """Different filenames should produce different IDs."""
        id1 = compute_file_id("file1.txt", 1024, "abc123")
        id2 = compute_file_id("file2.txt", 1024, "abc123")
        assert id1 != id2
    
    def test_different_size_different_id(self):
        """Different file sizes should produce different IDs."""
        id1 = compute_file_id("test.txt", 1024, "abc123")
        id2 = compute_file_id("test.txt", 2048, "abc123")
        assert id1 != id2
    
    def test_different_hash_different_id(self):
        """Different first chunk hashes should produce different IDs."""
        id1 = compute_file_id("test.txt", 1024, "abc123")
        id2 = compute_file_id("test.txt", 1024, "def456")
        assert id1 != id2
    
    def test_returns_64_char_hex(self):
        """File ID should be 64-char hex string."""
        file_id = compute_file_id("test.txt", 1024, "abc123")
        assert len(file_id) == 64
        assert all(c in '0123456789abcdef' for c in file_id)


class TestHashString:
    """Tests for hash_string function."""
    
    def test_simple_string(self):
        """Simple string should hash correctly."""
        result = hash_string("hello")
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert result == expected
    
    def test_unicode_string(self):
        """Unicode strings should hash correctly."""
        result = hash_string("héllo wörld 🌍")
        assert len(result) == 64


class TestShortHash:
    """Tests for short_hash function."""
    
    def test_default_length(self):
        """Default should return 8 characters."""
        full_hash = "abcdef0123456789" * 4  # 64 chars
        result = short_hash(full_hash)
        assert result == "abcdef01"
        assert len(result) == 8
    
    def test_custom_length(self):
        """Custom length should work."""
        full_hash = "abcdef0123456789" * 4
        result = short_hash(full_hash, length=12)
        assert result == "abcdef012345"
        assert len(result) == 12


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    import traceback
    
    print("Running Hashing Tests")
    print("=" * 60)
    
    test_classes = [
        TestComputeChunkHash,
        TestVerifyChunk,
        TestComputeFileId,
        TestHashString,
        TestShortHash
    ]
    
    total_tests = 0
    passed_tests = 0
    failed_tests = []
    
    for test_class in test_classes:
        print(f"\n{test_class.__name__}")
        print("-" * 40)
        
        instance = test_class()
        test_methods = [m for m in dir(instance) if m.startswith('test_')]
        
        for method_name in test_methods:
            total_tests += 1
            method = getattr(instance, method_name)
            try:
                method()
                print(f"  ✓ {method_name}")
                passed_tests += 1
            except AssertionError as e:
                print(f"  ✗ {method_name}: {e}")
                failed_tests.append((test_class.__name__, method_name, str(e)))
            except Exception as e:
                print(f"  ✗ {method_name}: {type(e).__name__}: {e}")
                failed_tests.append((test_class.__name__, method_name, traceback.format_exc()))
    
    print()
    print("=" * 60)
    print(f"Results: {passed_tests}/{total_tests} tests passed")
    
    if failed_tests:
        print(f"\nFailed tests ({len(failed_tests)}):")
        for class_name, method_name, error in failed_tests:
            print(f"  - {class_name}.{method_name}")
        sys.exit(1)
    else:
        print("\nAll tests passed! ✓")
        sys.exit(0)
