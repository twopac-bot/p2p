"""
Unit tests for the tracker components.

Tests include:
- TrackerStore unit tests (file metadata, peer registration, deduplication)
- TrackerServer integration tests (TCP JSON protocol)
- Concurrent access stress tests

Run with: python -m pytest tests/test_tracker.py -v
Or directly: python tests/test_tracker.py
"""

import sys
import asyncio
import json
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tracker.tracker_store import TrackerStore, FileMetadata, PeerInfo
from tracker.tracker_server import TrackerServer, TrackerClient, TrackerProtocol
from utils.config import Config


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


class TestTrackerStoreDuplicatePeer:
    """Tests for duplicate peer handling."""
    
    def setup_method(self):
        """Create fresh store for each test."""
        self.store = TrackerStore()
    
    def test_duplicate_peer_same_chunk_deduplicates(self):
        """Same peer registering same chunk twice should deduplicate."""
        self.store.register_peer("file1", 0, ("192.168.1.10", 8001))
        self.store.register_peer("file1", 0, ("192.168.1.10", 8001))
        self.store.register_peer("file1", 0, ("192.168.1.10", 8001))
        
        peers = self.store.get_peers("file1")
        # Should only have one entry for this peer
        assert len(peers[0]) == 1
        assert ("192.168.1.10", 8001) in peers[0]
    
    def test_same_peer_different_chunks(self):
        """Same peer can register for multiple chunks."""
        peer = ("192.168.1.10", 8001)
        self.store.register_peer("file1", 0, peer)
        self.store.register_peer("file1", 1, peer)
        self.store.register_peer("file1", 2, peer)
        
        peers = self.store.get_peers("file1")
        assert peer in peers[0]
        assert peer in peers[1]
        assert peer in peers[2]
    
    def test_same_peer_different_files(self):
        """Same peer can share different files."""
        peer = ("192.168.1.10", 8001)
        self.store.register_peer("file1", 0, peer)
        self.store.register_peer("file2", 0, peer)
        
        peers1 = self.store.get_peers("file1")
        peers2 = self.store.get_peers("file2")
        
        assert peer in peers1[0]
        assert peer in peers2[0]


class TestTrackerStoreMissingFile:
    """Tests for handling missing files."""
    
    def setup_method(self):
        """Create fresh store for each test."""
        self.store = TrackerStore()
    
    def test_get_metadata_missing_returns_none(self):
        """Getting metadata for missing file returns None, not exception."""
        result = self.store.get_file_metadata("nonexistent_file_id")
        assert result is None
    
    def test_get_peers_missing_file_returns_empty(self):
        """Getting peers for missing file returns empty dict, not exception."""
        result = self.store.get_peers("nonexistent_file_id")
        assert result == {}
    
    def test_get_peers_for_chunk_missing_returns_empty(self):
        """Getting peers for chunk of missing file returns empty list."""
        result = self.store.get_peers_for_chunk("nonexistent", 0)
        assert result == []
    
    def test_unregister_from_missing_file_returns_zero(self):
        """Unregistering from missing file returns 0, not exception."""
        result = self.store.unregister_peer("nonexistent", ("192.168.1.10", 8001))
        assert result == 0


class TestTrackerStoreRemovePeer:
    """Tests for remove_peer functionality (removing from all files)."""
    
    def setup_method(self):
        """Create fresh store for each test."""
        self.store = TrackerStore()
    
    def test_unregister_removes_from_all_chunks(self):
        """Unregistering peer removes it from all chunks of a file."""
        peer = ("192.168.1.10", 8001)
        
        # Register peer for multiple chunks
        self.store.register_peer("file1", 0, peer)
        self.store.register_peer("file1", 1, peer)
        self.store.register_peer("file1", 2, peer)
        self.store.register_peer("file1", 3, peer)
        
        # Unregister
        removed = self.store.unregister_peer("file1", peer)
        
        assert removed == 4
        peers = self.store.get_peers("file1")
        for chunk_idx in [0, 1, 2, 3]:
            assert peer not in peers.get(chunk_idx, [])
    
    def test_unregister_keeps_other_peers(self):
        """Unregistering one peer should not affect other peers."""
        peer1 = ("192.168.1.10", 8001)
        peer2 = ("192.168.1.11", 8001)
        
        self.store.register_peer("file1", 0, peer1)
        self.store.register_peer("file1", 0, peer2)
        self.store.register_peer("file1", 1, peer1)
        self.store.register_peer("file1", 1, peer2)
        
        # Remove peer1
        self.store.unregister_peer("file1", peer1)
        
        peers = self.store.get_peers("file1")
        assert peer2 in peers[0]
        assert peer2 in peers[1]
        assert peer1 not in peers[0]
        assert peer1 not in peers[1]


class TestTrackerStoreListFiles:
    """Tests for list_files functionality."""
    
    def setup_method(self):
        """Create fresh store for each test."""
        self.store = TrackerStore()
    
    def test_list_files_empty(self):
        """Empty store should return empty list."""
        result = self.store.list_files()
        assert result == []
    
    def test_list_files_returns_all_ids(self):
        """Should return all registered file IDs."""
        self.store.register_file_metadata("file_aaa", "a.txt", 1, ["h1"])
        self.store.register_file_metadata("file_bbb", "b.txt", 2, ["h1", "h2"])
        self.store.register_file_metadata("file_ccc", "c.txt", 3, ["h1", "h2", "h3"])
        
        result = self.store.list_files()
        
        assert len(result) == 3
        assert "file_aaa" in result
        assert "file_bbb" in result
        assert "file_ccc" in result


# =============================================================================
# Integration Tests for TrackerServer (TCP Protocol)
# =============================================================================

class TestTrackerServerIntegration:
    """Integration tests for TrackerServer using real TCP connections."""
    
    @staticmethod
    def run_async(coro):
        """Helper to run async code in sync tests."""
        return asyncio.get_event_loop().run_until_complete(coro)
    
    def setup_method(self):
        """Set up test fixtures."""
        # Use a random high port to avoid conflicts
        import random
        self.port = random.randint(10000, 60000)
        self.config = Config(tracker_host="127.0.0.1", tracker_port=self.port)
        self.store = TrackerStore()
        self.server = None
        self.server_task = None
    
    def teardown_method(self):
        """Clean up server."""
        if self.server_task:
            self.server_task.cancel()
            try:
                self.run_async(asyncio.sleep(0.1))
            except:
                pass
    
    async def _start_server(self):
        """Start the tracker server in background."""
        self.server = TrackerServer(self.config, self.store)
        # Start server but don't block
        server_coro = asyncio.start_server(
            self.server._handle_client,
            host=self.config.tracker_host,
            port=self.config.tracker_port
        )
        self._tcp_server = await server_coro
        return self._tcp_server
    
    async def _stop_server(self):
        """Stop the tracker server."""
        if hasattr(self, '_tcp_server') and self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
    
    async def _send_command(self, command: dict) -> dict:
        """Send a command to the server and get response."""
        reader, writer = await asyncio.open_connection(
            self.config.tracker_host,
            self.config.tracker_port
        )
        try:
            # Send command
            data = json.dumps(command).encode('utf-8') + b'\n'
            writer.write(data)
            await writer.drain()
            
            # Read response
            response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            return json.loads(response_line.decode('utf-8'))
        finally:
            writer.close()
            await writer.wait_closed()
    
    def test_register_file_command(self):
        """REGISTER_FILE command should work."""
        async def run_test():
            await self._start_server()
            try:
                response = await self._send_command({
                    "cmd": "REGISTER_FILE",
                    "file_id": "test_file_123",
                    "filename": "movie.mp4",
                    "total_chunks": 10,
                    "chunk_hashes": ["hash" + str(i) for i in range(10)],
                    "file_size": 10485760
                })
                
                assert response["status"] == "ok"
                assert response["registered"] is True
                
                # Verify it's stored
                meta = self.store.get_file_metadata("test_file_123")
                assert meta is not None
                assert meta.filename == "movie.mp4"
            finally:
                await self._stop_server()
        
        self.run_async(run_test())
    
    def test_register_peer_command(self):
        """REGISTER command should register a peer for a chunk."""
        async def run_test():
            await self._start_server()
            try:
                # First register the file
                await self._send_command({
                    "cmd": "REGISTER_FILE",
                    "file_id": "test_file",
                    "filename": "test.txt",
                    "total_chunks": 5,
                    "chunk_hashes": ["h"] * 5
                })
                
                # Register peer for chunk 0
                response = await self._send_command({
                    "cmd": "REGISTER",
                    "file_id": "test_file",
                    "chunk_index": 0,
                    "peer_host": "192.168.1.100",
                    "peer_port": 9001
                })
                
                assert response["status"] == "ok"
                
                # Verify peer is stored
                peers = self.store.get_peers("test_file")
                assert ("192.168.1.100", 9001) in peers[0]
            finally:
                await self._stop_server()
        
        self.run_async(run_test())
    
    def test_get_peers_command(self):
        """GET_PEERS command should return metadata and peer list."""
        async def run_test():
            await self._start_server()
            try:
                # Setup: register file and peers
                self.store.register_file_metadata(
                    "file_abc",
                    "document.pdf",
                    3,
                    ["hash0", "hash1", "hash2"],
                    file_size=3072
                )
                self.store.register_peer("file_abc", 0, ("10.0.0.1", 8001))
                self.store.register_peer("file_abc", 1, ("10.0.0.2", 8001))
                self.store.register_peer("file_abc", 0, ("10.0.0.3", 8001))
                
                # Query
                response = await self._send_command({
                    "cmd": "GET_PEERS",
                    "file_id": "file_abc"
                })
                
                assert response["status"] == "ok"
                assert response["file_id"] == "file_abc"
                
                # Check metadata
                metadata = response["metadata"]
                assert metadata["filename"] == "document.pdf"
                assert metadata["total_chunks"] == 3
                assert len(metadata["chunk_hashes"]) == 3
                
                # Check peers - keys should be strings for JSON compatibility
                peers = response["peers"]
                assert "0" in peers  # String key
                assert len(peers["0"]) == 2  # Two peers for chunk 0
            finally:
                await self._stop_server()
        
        self.run_async(run_test())
    
    def test_get_peers_missing_file(self):
        """GET_PEERS for missing file should return not_found."""
        async def run_test():
            await self._start_server()
            try:
                response = await self._send_command({
                    "cmd": "GET_PEERS",
                    "file_id": "nonexistent_file_id"
                })
                
                assert response["status"] == "not_found"
                assert "error" in response
            finally:
                await self._stop_server()
        
        self.run_async(run_test())
    
    def test_unknown_command(self):
        """Unknown command should return error."""
        async def run_test():
            await self._start_server()
            try:
                response = await self._send_command({
                    "cmd": "INVALID_COMMAND_XYZ"
                })
                
                assert response["status"] == "error"
                assert "unknown" in response.get("error", "").lower()
            finally:
                await self._stop_server()
        
        self.run_async(run_test())
    
    def test_ping_command(self):
        """PING command should return pong."""
        async def run_test():
            await self._start_server()
            try:
                response = await self._send_command({"cmd": "PING"})
                
                assert response["status"] == "ok"
                assert response["message"] == "pong"
                assert "timestamp" in response
            finally:
                await self._stop_server()
        
        self.run_async(run_test())
    
    def test_invalid_json(self):
        """Invalid JSON should return error response."""
        async def run_test():
            await self._start_server()
            try:
                reader, writer = await asyncio.open_connection(
                    self.config.tracker_host,
                    self.config.tracker_port
                )
                try:
                    # Send invalid JSON
                    writer.write(b'not valid json at all\n')
                    await writer.drain()
                    
                    response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    response = json.loads(response_line.decode('utf-8'))
                    
                    assert response["status"] == "error"
                    assert "json" in response.get("error", "").lower()
                finally:
                    writer.close()
                    await writer.wait_closed()
            finally:
                await self._stop_server()
        
        self.run_async(run_test())
    
    def test_json_keys_are_strings(self):
        """All JSON response keys should be strings (Android compatibility)."""
        async def run_test():
            await self._start_server()
            try:
                # Register file and peer
                self.store.register_file_metadata("f1", "test.txt", 2, ["h1", "h2"])
                self.store.register_peer("f1", 0, ("1.2.3.4", 8001))
                self.store.register_peer("f1", 1, ("1.2.3.4", 8001))
                
                response = await self._send_command({
                    "cmd": "GET_PEERS",
                    "file_id": "f1"
                })
                
                # Peers dict keys should be strings "0", "1", not ints
                peers = response["peers"]
                for key in peers.keys():
                    assert isinstance(key, str), f"Key {key} should be string, got {type(key)}"
            finally:
                await self._stop_server()
        
        self.run_async(run_test())


class TestTrackerConcurrentAsync:
    """Async stress tests using asyncio.gather."""
    
    def test_concurrent_async_registrations(self):
        """Many concurrent async registrations should work."""
        async def run_test():
            store = TrackerStore()
            store.register_file_metadata("stress_file", "big.zip", 1000, ["h"] * 1000)
            
            async def register_peer(peer_id):
                """Simulate a peer registering for random chunks."""
                import random
                chunks = random.sample(range(1000), 50)
                for chunk in chunks:
                    store.register_peer("stress_file", chunk, (f"10.0.{peer_id // 256}.{peer_id % 256}", 8001))
                return peer_id
            
            # Run 100 concurrent registrations
            tasks = [register_peer(i) for i in range(100)]
            results = await asyncio.gather(*tasks)
            
            assert len(results) == 100
            
            # Verify some registrations worked
            peers = store.get_peers("stress_file")
            assert len(peers) > 0
            
            # Each chunk should have some peers
            total_registrations = sum(len(p) for p in peers.values())
            assert total_registrations == 100 * 50  # 100 peers × 50 chunks each
        
        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# TrackerClient Tests
# =============================================================================

class TestTrackerClient:
    """Tests for TrackerClient helper class."""
    
    @staticmethod
    def run_async(coro):
        """Helper to run async code in sync tests."""
        return asyncio.get_event_loop().run_until_complete(coro)
    
    def setup_method(self):
        """Set up test fixtures."""
        import random
        self.port = random.randint(10000, 60000)
        self.config = Config(tracker_host="127.0.0.1", tracker_port=self.port)
        self.store = TrackerStore()
    
    def test_client_register_file(self):
        """TrackerClient.register_file should work."""
        async def run_test():
            # Start server
            server = TrackerServer(self.config, self.store)
            tcp_server = await asyncio.start_server(
                server._handle_client,
                host=self.config.tracker_host,
                port=self.config.tracker_port
            )
            
            try:
                # Use client
                client = TrackerClient(self.config)
                await client.connect()
                
                response = await client.register_file(
                    file_id="client_test_file",
                    filename="test.bin",
                    total_chunks=5,
                    chunk_hashes=["h1", "h2", "h3", "h4", "h5"]
                )
                
                await client.close()
                
                assert response["status"] == "ok"
                
                # Verify stored
                meta = self.store.get_file_metadata("client_test_file")
                assert meta is not None
            finally:
                tcp_server.close()
                await tcp_server.wait_closed()
        
        self.run_async(run_test())
    
    def test_client_get_peers(self):
        """TrackerClient.get_peers should work."""
        async def run_test():
            # Setup data
            self.store.register_file_metadata("f1", "file.txt", 2, ["h1", "h2"])
            self.store.register_peer("f1", 0, ("10.0.0.1", 8001))
            
            # Start server
            server = TrackerServer(self.config, self.store)
            tcp_server = await asyncio.start_server(
                server._handle_client,
                host=self.config.tracker_host,
                port=self.config.tracker_port
            )
            
            try:
                client = TrackerClient(self.config)
                await client.connect()
                
                response = await client.get_peers("f1")
                
                await client.close()
                
                assert response["status"] == "ok"
                assert "peers" in response
                assert "metadata" in response
            finally:
                tcp_server.close()
                await tcp_server.wait_closed()
        
        self.run_async(run_test())


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
        TestTrackerStoreThreadSafety,
        TestTrackerStoreDuplicatePeer,
        TestTrackerStoreMissingFile,
        TestTrackerStoreRemovePeer,
        TestTrackerStoreListFiles,
        TestTrackerServerIntegration,
        TestTrackerConcurrentAsync,
        TestTrackerClient,
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
                try:
                    instance.setup_method()
                except Exception as e:
                    print(f"  ✗ {method_name} (setup failed): {e}")
                    failed_tests.append((test_class.__name__, method_name, str(e)))
                    continue
            
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
