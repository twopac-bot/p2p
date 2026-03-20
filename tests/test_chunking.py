"""
Unit tests for the chunk_handler module.

Run with: python -m pytest tests/test_chunking.py -v
Or directly: python tests/test_chunking.py
"""

import sys
import tempfile
import shutil
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from peer.chunk_handler import (
    ChunkInfo,
    split_file,
    split_file_streaming,
    save_chunk,
    load_chunk,
    reassemble_file,
    cleanup_chunks,
    get_chunk_count,
    list_available_chunks
)
from utils.hashing import compute_chunk_hash


class TestChunkInfo:
    """Tests for ChunkInfo dataclass."""
    
    def test_creation(self):
        """Should create ChunkInfo correctly."""
        info = ChunkInfo(
            chunk_index=0,
            data=b"test data",
            hash="abc123",
            size=9
        )
        assert info.chunk_index == 0
        assert info.data == b"test data"
        assert info.hash == "abc123"
        assert info.size == 9
    
    def test_to_dict_without_data(self):
        """to_dict should exclude data by default."""
        info = ChunkInfo(chunk_index=1, data=b"secret", hash="xyz", size=6)
        result = info.to_dict(include_data=False)
        assert "data" not in result
        assert result["chunk_index"] == 1
        assert result["hash"] == "xyz"
        assert result["size"] == 6
    
    def test_to_dict_with_data(self):
        """to_dict should include base64 data when requested."""
        import base64
        info = ChunkInfo(chunk_index=0, data=b"hello", hash="abc", size=5)
        result = info.to_dict(include_data=True)
        assert "data" in result
        assert base64.b64decode(result["data"]) == b"hello"
    
    def test_from_dict(self):
        """from_dict should reconstruct ChunkInfo."""
        import base64
        data_dict = {
            "chunk_index": 2,
            "hash": "hash123",
            "size": 10,
            "data": base64.b64encode(b"chunk data").decode('ascii')
        }
        info = ChunkInfo.from_dict(data_dict)
        assert info.chunk_index == 2
        assert info.hash == "hash123"
        assert info.size == 10
        assert info.data == b"chunk data"


class TestSplitFile:
    """Tests for split_file function."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.test_file = self.test_dir / "test_input.bin"
    
    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_single_chunk_file(self):
        """File smaller than chunk size should produce one chunk."""
        content = b"Small file content"
        self.test_file.write_bytes(content)
        
        chunks = split_file(str(self.test_file), chunk_size=1024)
        
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0
        assert chunks[0].data == content
        assert chunks[0].size == len(content)
    
    def test_exact_chunk_size(self):
        """File exactly chunk size should produce one chunk."""
        content = b"X" * 100
        self.test_file.write_bytes(content)
        
        chunks = split_file(str(self.test_file), chunk_size=100)
        
        assert len(chunks) == 1
        assert chunks[0].size == 100
    
    def test_multiple_chunks(self):
        """File larger than chunk size should produce multiple chunks."""
        content = b"A" * 250
        self.test_file.write_bytes(content)
        
        chunks = split_file(str(self.test_file), chunk_size=100)
        
        assert len(chunks) == 3
        assert chunks[0].size == 100
        assert chunks[1].size == 100
        assert chunks[2].size == 50  # Remainder
    
    def test_chunks_are_sequential(self):
        """Chunk indices should be sequential starting from 0."""
        content = b"B" * 300
        self.test_file.write_bytes(content)
        
        chunks = split_file(str(self.test_file), chunk_size=100)
        
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i
    
    def test_hashes_are_computed(self):
        """Each chunk should have a valid hash."""
        content = b"Test content for hashing"
        self.test_file.write_bytes(content)
        
        chunks = split_file(str(self.test_file), chunk_size=1024)
        
        assert chunks[0].hash == compute_chunk_hash(content)
    
    def test_file_not_found(self):
        """Should raise FileNotFoundError for missing file."""
        try:
            split_file("/nonexistent/file.txt")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass
    
    def test_progress_callback(self):
        """Progress callback should be called."""
        content = b"X" * 500
        self.test_file.write_bytes(content)
        
        progress_calls = []
        def callback(current, total):
            progress_calls.append((current, total))
        
        chunks = split_file(str(self.test_file), chunk_size=100, progress_callback=callback)
        
        assert len(progress_calls) == len(chunks)
        assert progress_calls[-1] == (5, 5)


class TestSplitFileStreaming:
    """Tests for split_file_streaming generator."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.test_file = self.test_dir / "test_stream.bin"
    
    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_yields_chunks(self):
        """Should yield chunks one at a time."""
        content = b"C" * 300
        self.test_file.write_bytes(content)
        
        chunks = list(split_file_streaming(str(self.test_file), chunk_size=100))
        
        assert len(chunks) == 3
    
    def test_is_generator(self):
        """Should return a generator, not a list."""
        content = b"D" * 100
        self.test_file.write_bytes(content)
        
        result = split_file_streaming(str(self.test_file), chunk_size=100)
        
        # Check it's a generator
        assert hasattr(result, '__iter__')
        assert hasattr(result, '__next__')


class TestSaveAndLoadChunk:
    """Tests for save_chunk and load_chunk functions."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.chunks_dir = self.test_dir / "chunks"
    
    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_save_and_load(self):
        """Should save and load chunk correctly."""
        data = b"chunk content here"
        
        save_chunk(0, data, str(self.chunks_dir))
        loaded = load_chunk(0, str(self.chunks_dir))
        
        assert loaded == data
    
    def test_save_with_file_id(self):
        """Should create subdirectory for file_id."""
        data = b"organized chunk"
        file_id = "test_file_abc"
        
        path = save_chunk(5, data, str(self.chunks_dir), file_id=file_id)
        
        assert file_id in path
        assert "chunk_000005.dat" in path
    
    def test_load_with_file_id(self):
        """Should load from file_id subdirectory."""
        data = b"file specific chunk"
        file_id = "my_file_123"
        
        save_chunk(3, data, str(self.chunks_dir), file_id=file_id)
        loaded = load_chunk(3, str(self.chunks_dir), file_id=file_id)
        
        assert loaded == data
    
    def test_load_nonexistent_raises(self):
        """Should raise FileNotFoundError for missing chunk."""
        try:
            load_chunk(999, str(self.chunks_dir))
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass
    
    def test_chunk_filename_format(self):
        """Chunk filenames should be zero-padded."""
        data = b"test"
        path = save_chunk(42, data, str(self.chunks_dir))
        
        assert "chunk_000042.dat" in path


class TestReassembleFile:
    """Tests for reassemble_file function."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.chunks_dir = self.test_dir / "chunks"
        self.output_file = self.test_dir / "output.bin"
    
    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_reassemble_single_chunk(self):
        """Should reassemble single chunk file."""
        content = b"Single chunk content"
        file_id = "single_chunk"
        chunk_hash = compute_chunk_hash(content)
        
        save_chunk(0, content, str(self.chunks_dir), file_id=file_id)
        
        success = reassemble_file(
            str(self.chunks_dir),
            str(self.output_file),
            [chunk_hash],
            file_id=file_id
        )
        
        assert success is True
        assert self.output_file.read_bytes() == content
    
    def test_reassemble_multiple_chunks(self):
        """Should reassemble multiple chunks in order."""
        chunks = [b"AAAA", b"BBBB", b"CC"]
        file_id = "multi_chunk"
        hashes = [compute_chunk_hash(c) for c in chunks]
        
        for i, data in enumerate(chunks):
            save_chunk(i, data, str(self.chunks_dir), file_id=file_id)
        
        success = reassemble_file(
            str(self.chunks_dir),
            str(self.output_file),
            hashes,
            file_id=file_id
        )
        
        assert success is True
        assert self.output_file.read_bytes() == b"AAAABBBBCC"
    
    def test_verify_hash_failure(self):
        """Should raise ValueError on hash mismatch when verify=True."""
        content = b"original"
        file_id = "verify_fail"
        wrong_hash = compute_chunk_hash(b"different")
        
        save_chunk(0, content, str(self.chunks_dir), file_id=file_id)
        
        try:
            reassemble_file(
                str(self.chunks_dir),
                str(self.output_file),
                [wrong_hash],
                file_id=file_id,
                verify=True
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "verification failed" in str(e).lower()
    
    def test_skip_verification(self):
        """Should skip verification when verify=False."""
        content = b"content"
        file_id = "no_verify"
        wrong_hash = "wrong_hash_value"
        
        save_chunk(0, content, str(self.chunks_dir), file_id=file_id)
        
        # Should not raise even with wrong hash
        success = reassemble_file(
            str(self.chunks_dir),
            str(self.output_file),
            [wrong_hash],
            file_id=file_id,
            verify=False
        )
        
        assert success is True


class TestCleanupChunks:
    """Tests for cleanup_chunks function."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.chunks_dir = self.test_dir / "chunks"
    
    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_cleanup_removes_chunks(self):
        """Should remove all chunk files for a file_id."""
        file_id = "cleanup_test"
        
        for i in range(5):
            save_chunk(i, b"data", str(self.chunks_dir), file_id=file_id)
        
        deleted = cleanup_chunks(str(self.chunks_dir), file_id)
        
        assert deleted == 5
        assert not (self.chunks_dir / file_id).exists()
    
    def test_cleanup_nonexistent_returns_zero(self):
        """Should return 0 for nonexistent file_id."""
        deleted = cleanup_chunks(str(self.chunks_dir), "nonexistent")
        assert deleted == 0


class TestGetChunkCount:
    """Tests for get_chunk_count function."""
    
    def test_exact_division(self):
        """File size exactly divisible by chunk size."""
        assert get_chunk_count(1000, chunk_size=100) == 10
    
    def test_with_remainder(self):
        """File size with remainder should round up."""
        assert get_chunk_count(1001, chunk_size=100) == 11
    
    def test_smaller_than_chunk(self):
        """File smaller than chunk size should be 1 chunk."""
        assert get_chunk_count(50, chunk_size=100) == 1
    
    def test_empty_file(self):
        """Empty file should be 0 chunks."""
        assert get_chunk_count(0, chunk_size=100) == 0


class TestListAvailableChunks:
    """Tests for list_available_chunks function."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.chunks_dir = self.test_dir / "chunks"
    
    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_no_chunks(self):
        """Should return empty list when no chunks exist."""
        result = list_available_chunks(str(self.chunks_dir), "nonexistent")
        assert result == []
    
    def test_some_chunks(self):
        """Should return sorted list of available chunks."""
        file_id = "partial"
        
        for i in [0, 2, 5, 3]:  # Non-sequential order
            save_chunk(i, b"x", str(self.chunks_dir), file_id=file_id)
        
        result = list_available_chunks(str(self.chunks_dir), file_id)
        
        assert result == [0, 2, 3, 5]  # Should be sorted


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    import traceback
    
    print("Running Chunk Handler Tests")
    print("=" * 60)
    
    test_classes = [
        TestChunkInfo,
        TestSplitFile,
        TestSplitFileStreaming,
        TestSaveAndLoadChunk,
        TestReassembleFile,
        TestCleanupChunks,
        TestGetChunkCount,
        TestListAvailableChunks
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
            
            # Setup
            if hasattr(instance, 'setup_method'):
                instance.setup_method()
            
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
            finally:
                # Teardown
                if hasattr(instance, 'teardown_method'):
                    try:
                        instance.teardown_method()
                    except:
                        pass
    
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
