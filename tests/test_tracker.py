"""
Unit tests for the tracker components.

Run with: python -m pytest tests/test_tracker.py -v
Or directly: python tests/test_tracker.py
"""

import sys
import asyncio
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tracker.tracker_store import TrackerStore, FileMetadata, PeerInfo


class TestFileMetadata:
    """Tests for FileMetadata dataclass."""
    
    def test_creation(self):
        """Should create FileMetadata correctly."""
        meta = FileMetadata(
            file_id="abc123",
            filename="test.txt",
            total_chunks=10,
            chunk_hashes=["h" + str(i) for i in range(10)]
        )
        assert meta.file_id == "abc123"
        assert meta.filename == "test.txt"
        assert meta.total_chunks == 10
        assert len(meta.chunk_hashes) == 10
    
    def test_to_dict(self):
        """to_dict should serialize correctly."""
        meta = FileMetadata(
            file_id="abc123",
            filename="test.txt",
            total_chunks=5,
            chunk_hashes=["h1", "h2", "h3", "h4", "h5"],
            file_size=1024
        )
        d = meta.to_dict()
        assert d["file_id"] == "abc123"
        assert d["total_chunks"] == 5
        assert len(d["chunk_hashes"]) == 5
    
    def test_from_dict(self):
        """from_dict should deserialize correctly."""
        data = {
            "file_id": "xyz789",
            "filename": "movie.mp4",
            "total_chunks": 100,
            "chunk_hashes": ["hash"] * 100,
            "file_size": 104857600
        }
        meta = FileMetadata.from_dict(data)
        assert meta.file_id == "xyz789"
        assert meta.filename == "movie.mp4"
        assert meta.total_chunks == 100


class TestTrackerStore:
    """Tests for TrackerStore class."""
    
    def setup_method(self):
        """Create fresh store for each test."""
        self.store = TrackerStore()
    
    def test_register_file_metadata(self):
        """Should register file metadata."""
        result = self.store.register_file_metadata(
            file_id="file1",
            filename="test.txt",
            total_chunks=5,
            chunk_hashes=["h1", "h2", "h3", "h4", "h5"]
        )
        assert result is True
        
        meta = self.store.get_file_metadata("file1")
        assert meta is not None
        assert meta.filename == "test.txt"
    
    def test_register_duplicate_file(self):
        """Duplicate registration should return False."""
        self.store.register_file_metadata("file1", "test.txt", 5, ["h"] * 5)
        result = self.store.register_file_metadata("file1", "test.txt", 5, ["h"] * 5)
        assert result is False
    
    def test_get_nonexistent_metadata(self):
        """Getting nonexistent file should return None."""
        result = self.store.get_file_metadata("nonexistent")
        assert result is None
    
    def test_register_peer(self):
        """Should register a peer for a chunk."""
        self.store.register_file_metadata("file1", "test.txt", 5, ["h"] * 5)
        
        result = self.store.register_peer("file1", 0, ("192.168.1.10", 8001))
        assert result is True
        
        peers = self.store.get_peers("file1")
        assert 0 in peers
        assert ("192.168.1.10", 8001) in peers[0]
    
    def test_register_peer_update(self):
        """Re-registering same peer should update and return False."""
        self.store.register_peer("file1", 0, ("192.168.1.10", 8001))
        result = self.store.register_peer("file1", 0, ("192.168.1.10", 8001))
        assert result is False
    
    def test_multiple_peers_per_chunk(self):
        """Multiple peers can have the same chunk."""
        self.store.register_peer("file1", 0, ("192.168.1.10", 8001))
        self.store.register_peer("file1", 0, ("192.168.1.11", 8001))
        self.store.register_peer("file1", 0, ("192.168.1.12", 8001))
        
        peers = self.store.get_peers("file1")
        assert len(peers[0]) == 3
    
    def test_register_peer_batch(self):
        """Batch registration should work."""
        count = self.store.register_peer_chunks(
            "file1",
            [0, 1, 2, 3, 4],
            ("192.168.1.10", 8001)
        )
        assert count == 5
        
        peers = self.store.get_peers("file1")
        assert len(peers) == 5
    
    def test_unregister_peer(self):
        """Should unregister peer from all chunks."""
        self.store.register_peer_chunks("file1", [0, 1, 2], ("192.168.1.10", 8001))
        self.store.register_peer_chunks("file1", [0, 1], ("192.168.1.11", 8001))
        
        removed = self.store.unregister_peer("file1", ("192.168.1.10", 8001))
        assert removed == 3
        
        peers = self.store.get_peers("file1")
        # Peer 1.11 should still be there for chunks 0 and 1
        assert ("192.168.1.10", 8001) not in peers.get(0, [])
        assert ("192.168.1.11", 8001) in peers[0]
    
    def test_get_peers_for_chunk(self):
        """Should get peers for specific chunk."""
        self.store.register_peer("file1", 0, ("192.168.1.10", 8001))
        self.store.register_peer("file1", 1, ("192.168.1.11", 8001))
        
        peers_0 = self.store.get_peers_for_chunk("file1", 0)
        peers_1 = self.store.get_peers_for_chunk("file1", 1)
        
        assert ("192.168.1.10", 8001) in peers_0
        assert ("192.168.1.10", 8001) not in peers_1
        assert ("192.168.1.11", 8001) in peers_1
    
    def test_get_stats(self):
        """Should return correct statistics."""
        self.store.register_file_metadata("file1", "test.txt", 5, ["h"] * 5)
        self.store.register_peer_chunks("file1", [0, 1, 2], ("192.168.1.10", 8001))
        
        stats = self.store.get_stats()
        assert stats["files"] == 1
        assert stats["total_peer_registrations"] == 3
    
    def test_file_exists(self):
        """file_exists should work correctly."""
        assert self.store.file_exists("nonexistent") is False
        
        self.store.register_file_metadata("file1", "test.txt", 5, ["h"] * 5)
        assert self.store.file_exists("file1") is True
    
    def test_list_files(self):
        """list_files should return all file IDs."""
        self.store.register_file_metadata("file1", "a.txt", 1, ["h"])
        self.store.register_file_metadata("file2", "b.txt", 1, ["h"])
        self.store.register_file_metadata("file3", "c.txt", 1, ["h"])
        
        files = self.store.list_files()
        assert len(files) == 3
        assert "file1" in files
        assert "file2" in files
        assert "file3" in files


class TestTrackerStoreThreadSafety:
    """Tests for thread safety (basic verification)."""
    
    def test_concurrent_registrations(self):
        """Multiple concurrent registrations should not crash."""
        import threading
        
        store = TrackerStore()
        store.register_file_metadata("file1", "test.txt", 100, ["h"] * 100)
        
        errors = []
        
        def register_chunks(peer_id):
            try:
                for i in range(100):
                    store.register_peer("file1", i, (f"192.168.1.{peer_id}", 8001))
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=register_chunks, args=(i,)) for i in range(10)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        
        # Each chunk should have 10 peers
        peers = store.get_peers("file1")
        for i in range(100):
            assert len(peers[i]) == 10


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    import traceback
    
    print("Running Tracker Tests")
    print("=" * 60)
    
    test_classes = [
        TestFileMetadata,
        TestTrackerStore,
        TestTrackerStoreThreadSafety
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
