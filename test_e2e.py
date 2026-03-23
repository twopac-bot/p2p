#!/usr/bin/env python3
"""
End-to-End Test for P2P File Sharing System
============================================
This script tests the complete workflow without CLI/Rich dependencies.

Run with: python test_e2e.py
"""

import asyncio
import hashlib
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from utils.config import Config
from peer.peer_node import PeerNode
from tracker.tracker_server import TrackerServer


def create_test_file(path: str, size_mb: int = 5) -> str:
    """Create a random binary test file and return its SHA256 hash."""
    print(f"Creating {size_mb}MB test file: {path}")
    
    # Create random data
    data = os.urandom(size_mb * 1024 * 1024)
    
    with open(path, 'wb') as f:
        f.write(data)
    
    # Compute hash
    file_hash = hashlib.sha256(data).hexdigest()
    print(f"  Size: {len(data):,} bytes")
    print(f"  SHA256: {file_hash}")
    return file_hash


def compute_file_hash(path: str) -> str:
    """Compute SHA256 hash of a file."""
    hasher = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


async def run_test():
    """Run the complete E2E test."""
    
    print("=" * 60)
    print("P2P File Sharing System - End-to-End Test")
    print("=" * 60)
    print()
    
    # Test configuration
    test_dir = Path("./test_e2e_temp")
    test_file = test_dir / "test_input.bin"
    output_dir = test_dir / "downloads"
    
    # Clean up any previous test artifacts
    if test_dir.exists():
        shutil.rmtree(test_dir)
    
    test_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    
    # Create config
    config = Config(
        tracker_host="127.0.0.1",
        tracker_port=8000,
        peer_port=8001,
        download_dir=str(output_dir),
        chunks_dir=str(test_dir / "chunks"),
        progress_dir=str(test_dir / "progress"),
        uploads_dir=str(test_dir / "uploads")
    )
    config.ensure_directories()
    
    tracker_server = None
    tracker_task = None
    
    try:
        # ============================================================
        # Step 1: Create test file
        # ============================================================
        print("=== Step 1: Creating test file ===")
        original_hash = create_test_file(str(test_file), size_mb=5)
        print("✓ Test file created\n")
        
        # ============================================================
        # Step 2: Start tracker server
        # ============================================================
        print("=== Step 2: Starting tracker server ===")
        tracker_server = TrackerServer(config)
        
        # Run tracker in background
        async def run_tracker():
            try:
                await tracker_server.start()
            except asyncio.CancelledError:
                pass
        
        tracker_task = asyncio.create_task(run_tracker())
        
        # Wait for tracker to be ready
        await asyncio.sleep(1)
        print(f"  Tracker running on {config.tracker_host}:{config.tracker_port}")
        print("✓ Tracker started\n")
        
        # ============================================================
        # Step 3: Upload file
        # ============================================================
        print("=== Step 3: Uploading file ===")
        
        uploader_node = PeerNode(config)
        
        upload_result = await uploader_node.upload(
            filepath=str(test_file),
            peer_host="127.0.0.1",
            peer_port=8001
        )
        
        if not upload_result.success:
            print(f"✗ Upload failed: {upload_result.error}")
            return False
        
        file_id = upload_result.file_id
        print(f"  File ID: {file_id}")
        print(f"  Chunks: {upload_result.total_chunks}")
        print(f"  Peer port: {upload_result.peer_port}")
        print("✓ Upload successful\n")
        
        # Give uploader time to fully start
        await asyncio.sleep(1)
        
        # ============================================================
        # Step 4: Download file
        # ============================================================
        print("=== Step 4: Downloading file ===")
        
        # Create separate downloader node (simulating different peer)
        downloader_config = Config(
            tracker_host="127.0.0.1",
            tracker_port=8000,
            peer_port=8002,  # Different port
            download_dir=str(output_dir),
            chunks_dir=str(test_dir / "download_chunks"),
            progress_dir=str(test_dir / "download_progress")
        )
        downloader_config.ensure_directories()
        
        downloader_node = PeerNode(downloader_config)
        
        download_result = await downloader_node.download(
            file_id=file_id,
            output_dir=str(output_dir)
        )
        
        if not download_result.success:
            print(f"✗ Download failed: {download_result.error}")
            return False
        
        print(f"  Output: {download_result.output_path}")
        print(f"  Chunks: {download_result.downloaded_chunks}")
        print(f"  Bytes: {download_result.bytes_downloaded:,}")
        print("✓ Download successful\n")
        
        # ============================================================
        # Step 5: Verify integrity
        # ============================================================
        print("=== Step 5: Verifying file integrity ===")
        
        downloaded_hash = compute_file_hash(download_result.output_path)
        
        print(f"  Original hash:   {original_hash}")
        print(f"  Downloaded hash: {downloaded_hash}")
        
        if original_hash == downloaded_hash:
            print("✓ PASS: File integrity verified - hashes match!\n")
        else:
            print("✗ FAIL: Hash mismatch - file corrupted!\n")
            return False
        
        # ============================================================
        # Step 6: Run unit tests
        # ============================================================
        print("=== Step 6: Running unit tests ===")
        
        # Stop uploader before running pytest
        await uploader_node.stop_all()
        
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
            capture_output=False
        )
        
        if result.returncode == 0:
            print("✓ Unit tests passed\n")
        else:
            print("✗ Some unit tests failed\n")
            return False
        
        # ============================================================
        # Success!
        # ============================================================
        print("=" * 60)
        print("=== ALL TESTS PASSED ===")
        print("=" * 60)
        print()
        print("Test Summary:")
        print("  ✓ Created 5MB test file")
        print("  ✓ Tracker server started")
        print("  ✓ File uploaded and registered")
        print("  ✓ File downloaded from peer")
        print("  ✓ File integrity verified (SHA256 match)")
        print("  ✓ Unit tests passed")
        print()
        print("The P2P file sharing system is working correctly!")
        
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # ============================================================
        # Cleanup
        # ============================================================
        print("\n=== Cleanup ===")
        
        # Stop tracker
        if tracker_server:
            await tracker_server.stop()
        
        if tracker_task:
            tracker_task.cancel()
            try:
                await tracker_task
            except asyncio.CancelledError:
                pass
        
        # Remove test directory
        if test_dir.exists():
            shutil.rmtree(test_dir, ignore_errors=True)
        
        # Clean up main chunks/progress dirs if created
        for cleanup_dir in ["./chunks", "./progress", "./downloads/test_input.bin"]:
            p = Path(cleanup_dir)
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
        
        print("Cleanup complete")


def main():
    """Entry point."""
    success = asyncio.run(run_test())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
