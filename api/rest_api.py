"""
REST API layer for P2P file sharing system.

=====================================================================
SCAFFOLD ONLY - NOT YET IMPLEMENTED
=====================================================================

This module will provide a FastAPI-based REST API that wraps the
peer_node.py engine, enabling:

1. Android Integration (via Chaquopy or as a separate server)
2. Web-based clients
3. Third-party integrations

IMPLEMENTATION PLAN:
After the CLI (cli/main.py) is fully working and tested, implement
this module by wrapping peer_node.upload() and peer_node.download()
as REST endpoints.

ENDPOINTS TO IMPLEMENT:
-----------------------

POST /upload
    Request Body: multipart/form-data with file
    Response: { "file_id": "...", "filename": "...", "total_chunks": N }
    
    Wraps: peer_node.upload(filepath)
    
POST /download
    Request Body: { "file_id": "..." }
    Response: Streams file or returns { "status": "started", "progress_url": "/status/{file_id}" }
    
    Wraps: peer_node.download(file_id)

GET /status/{file_id}
    Response: {
        "file_id": "...",
        "filename": "...", 
        "total_chunks": N,
        "downloaded_chunks": N,
        "percent": 0.0-100.0,
        "speed_bps": N,
        "eta_seconds": N,
        "is_complete": bool
    }
    
    Wraps: progress_tracker.get_status(file_id)

GET /peers/{file_id}
    Response: {
        "file_id": "...",
        "total_peers": N,
        "chunks": {
            "0": [{"host": "...", "port": N}, ...],
            "1": [...],
            ...
        }
    }
    
    Wraps: Tracker client's get_peers()

DELETE /download/{file_id}
    Cancels an in-progress download and cleans up chunks.
    Response: { "status": "cancelled" }

WEBSOCKET ENDPOINT (optional future enhancement):
-------------------------------------------------

WS /ws/progress/{file_id}
    Streams real-time progress updates for Android progress bar.
    Messages: { "chunk_index": N, "percent": 0.0-100.0, "speed_bps": N }

ANDROID INTEGRATION NOTES:
--------------------------

Option A: Run this FastAPI server on Android via Chaquopy
    - Start server in a foreground service
    - Flutter/Kotlin UI calls localhost:8080 endpoints
    - Pros: Reuse Python code, rapid development
    - Cons: Python runtime overhead on mobile

Option B: Rewrite API in Kotlin with Ktor
    - Keep the same REST contract
    - Reimplement peer_node logic in Kotlin coroutines
    - Pros: Native performance, smaller APK
    - Cons: Code duplication, maintenance burden

Recommendation: Start with Option A for MVP, migrate to Option B
for production if performance is an issue.

AUTHENTICATION (TODO):
---------------------

For public deployments, add:
- API key authentication via header
- Rate limiting
- CORS configuration for web clients

=====================================================================
"""

from typing import Optional


# =============================================================================
# Stub function signatures (to be implemented after CLI is working)
# =============================================================================

async def upload_file(file_path: str) -> dict:
    """
    Upload a file to the P2P network.
    
    Args:
        file_path: Path to the file to upload
        
    Returns:
        Dictionary with file_id, filename, total_chunks
    """
    raise NotImplementedError("REST API not yet implemented - complete CLI first")


async def start_download(file_id: str) -> dict:
    """
    Start downloading a file from the P2P network.
    
    Args:
        file_id: ID of the file to download
        
    Returns:
        Dictionary with download status
    """
    raise NotImplementedError("REST API not yet implemented - complete CLI first")


async def get_download_status(file_id: str) -> dict:
    """
    Get the status of a download.
    
    Args:
        file_id: ID of the file being downloaded
        
    Returns:
        Dictionary with progress information
    """
    raise NotImplementedError("REST API not yet implemented - complete CLI first")


async def get_peers_for_file(file_id: str) -> dict:
    """
    Get the peer list for a file.
    
    Args:
        file_id: ID of the file
        
    Returns:
        Dictionary with peer information per chunk
    """
    raise NotImplementedError("REST API not yet implemented - complete CLI first")


async def cancel_download(file_id: str) -> dict:
    """
    Cancel an in-progress download.
    
    Args:
        file_id: ID of the download to cancel
        
    Returns:
        Dictionary with cancellation status
    """
    raise NotImplementedError("REST API not yet implemented - complete CLI first")


# =============================================================================
# FastAPI App Factory (to be implemented)
# =============================================================================

def create_app(config: Optional[object] = None):
    """
    Create the FastAPI application.
    
    Args:
        config: Optional Config object for dependency injection
        
    Returns:
        FastAPI application instance
        
    Example (after implementation):
        from api.rest_api import create_app
        from utils.config import Config
        
        config = Config(tracker_host="tracker.example.com")
        app = create_app(config)
        
        # Run with: uvicorn api.rest_api:app --host 0.0.0.0 --port 8080
    """
    raise NotImplementedError("REST API not yet implemented - complete CLI first")


# Placeholder for the FastAPI app instance
# After implementation: app = create_app()
app = None


# =============================================================================
# Module info
# =============================================================================

if __name__ == "__main__":
    print("P2P File Share REST API")
    print("=" * 50)
    print()
    print("STATUS: Scaffold only - not yet implemented")
    print()
    print("This module will be implemented after the CLI is working.")
    print("It will wrap peer_node.py as a REST API for Android integration.")
    print()
    print("Planned endpoints:")
    print("  POST   /upload          - Upload a file")
    print("  POST   /download        - Start a download")
    print("  GET    /status/{id}     - Get download progress")
    print("  GET    /peers/{id}      - Get peer list")
    print("  DELETE /download/{id}   - Cancel download")
