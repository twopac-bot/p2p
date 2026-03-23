"""
REST API for P2P File Sharing System.

A FastAPI-based REST API that wraps the PeerNode engine for:
- File uploads to the P2P network
- File downloads from the P2P network  
- Download status tracking
- Download cancellation

Run with:
    uvicorn api.rest_api:app --host 0.0.0.0 --port 8080 --reload
"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

# Add parent to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config, default_config
from utils.logger import get_logger, setup_logging
from peer.peer_node import PeerNode
from tracker.tracker_server import TrackerClient

# =============================================================================
# SECTION 1: Imports and Globals
# =============================================================================

# Initialize logger
logger = get_logger("api")

# Global PeerNode instance (initialized in lifespan)
peer_node: Optional[PeerNode] = None

# Track active download tasks: { file_id: asyncio.Task }
active_tasks: Dict[str, asyncio.Task] = {}


# =============================================================================
# SECTION 2: Pydantic Models
# =============================================================================

class DownloadRequest(BaseModel):
    """Request body for POST /download endpoint."""
    file_id: str
    
    @field_validator('file_id')
    @classmethod
    def file_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('file_id cannot be empty')
        return v.strip()


class HealthResponse(BaseModel):
    """Response for GET /health endpoint."""
    status: str
    peer_port: int
    tracker: str


class DownloadStartResponse(BaseModel):
    """Response for POST /download endpoint."""
    status: str
    file_id: str


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str


# =============================================================================
# SECTION 3: Lifespan Context Manager
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.
    
    Handles startup and shutdown of the PeerNode.
    """
    global peer_node
    
    # Startup
    logger.info("Starting P2P REST API...")
    setup_logging()
    
    # Create PeerNode with default config (reads from environment variables)
    peer_node = PeerNode(default_config)
    logger.info(
        "PeerNode initialized - Tracker: %s:%d, Peer port: %d",
        default_config.tracker_host,
        default_config.tracker_port,
        default_config.peer_port
    )
    
    yield
    
    # Shutdown
    logger.info("Shutting down P2P REST API...")
    
    # Cancel all active download tasks
    for file_id, task in list(active_tasks.items()):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        del active_tasks[file_id]
    
    # Stop all uploaders
    if peer_node:
        await peer_node.stop_all()
    
    logger.info("P2P REST API shutdown complete")


# =============================================================================
# SECTION 4: FastAPI App
# =============================================================================

app = FastAPI(
    title="P2P File Share API",
    description="REST API for peer-to-peer file sharing",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS - allow all origins for Flutter WebView and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# SECTION 5: Endpoints
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.
    
    Returns:
        Health status with peer port and tracker address.
        Always returns 200.
    """
    return HealthResponse(
        status="ok",
        peer_port=default_config.peer_port,
        tracker=f"{default_config.tracker_host}:{default_config.tracker_port}"
    )


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """
    Upload a file to the P2P network.
    
    Accepts a file via multipart/form-data, splits it into chunks,
    registers with the tracker, and starts serving chunks.
    
    Args:
        file: The file to upload (multipart/form-data)
        
    Returns:
        200: Upload result with file_id, filename, total_chunks, etc.
        500: Error details if upload failed
    """
    global peer_node
    
    if peer_node is None:
        raise HTTPException(status_code=500, detail="PeerNode not initialized")
    
    # Generate unique temp filename to avoid conflicts
    temp_filename = f"tmp_{uuid.uuid4().hex}_{file.filename}"
    temp_path = Path(default_config.uploads_dir) / temp_filename
    
    logger.info("Upload started: %s", file.filename)
    
    try:
        # Ensure uploads directory exists
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save uploaded file to temp location
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info("Saved temp file: %s (%d bytes)", temp_path, len(content))
        
        # Call PeerNode.upload()
        result = await peer_node.upload(
            filepath=str(temp_path),
            peer_host=default_config.tracker_host,
            peer_port=default_config.peer_port
        )
        
        if not result.success:
            logger.error("Upload failed: %s", result.error)
            raise HTTPException(status_code=500, detail=result.error or "Upload failed")
        
        logger.info(
            "Upload complete: file_id=%s, chunks=%d",
            result.file_id[:16] if result.file_id else "None",
            result.total_chunks
        )
        
        return result.to_dict()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Upload error: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Always clean up temp file
        if temp_path.exists():
            try:
                temp_path.unlink()
                logger.debug("Cleaned up temp file: %s", temp_path)
            except Exception as e:
                logger.warning("Failed to clean up temp file %s: %s", temp_path, e)


@app.post("/download", status_code=202, response_model=DownloadStartResponse)
async def start_download(request: DownloadRequest) -> DownloadStartResponse:
    """
    Start downloading a file from the P2P network.
    
    Initiates an async download task. Use GET /status/{file_id} to track progress.
    
    Args:
        request: JSON body with file_id
        
    Returns:
        202: Download started with file_id
        400: Invalid or empty file_id
    """
    global peer_node
    
    if peer_node is None:
        raise HTTPException(status_code=500, detail="PeerNode not initialized")
    
    file_id = request.file_id
    
    logger.info("Download requested: %s", file_id[:16] if len(file_id) > 16 else file_id)
    
    # Check if download is already in progress
    if file_id in active_tasks and not active_tasks[file_id].done():
        logger.info("Download already in progress: %s", file_id[:16])
        return DownloadStartResponse(status="already_started", file_id=file_id)
    
    # Create async download task (don't await it)
    async def download_task():
        try:
            result = await peer_node.download(file_id)
            if result.success:
                logger.info("Download complete: %s -> %s", file_id[:16], result.output_path)
            else:
                logger.error("Download failed: %s - %s", file_id[:16], result.error)
        except Exception as e:
            logger.exception("Download task error for %s: %s", file_id[:16], e)
        finally:
            # Clean up task reference when done
            if file_id in active_tasks:
                del active_tasks[file_id]
    
    task = asyncio.create_task(download_task())
    active_tasks[file_id] = task
    
    return DownloadStartResponse(status="started", file_id=file_id)


@app.get("/status/{file_id}")
async def get_status(file_id: str) -> dict:
    """
    Get download status for a file.
    
    Args:
        file_id: The file ID to check status for
        
    Returns:
        200: Status dictionary with progress information
        404: File not found or no active download
    """
    global peer_node
    
    if peer_node is None:
        raise HTTPException(status_code=500, detail="PeerNode not initialized")
    
    if not file_id or not file_id.strip():
        raise HTTPException(status_code=400, detail="file_id cannot be empty")
    
    file_id = file_id.strip()
    
    status = await peer_node.get_status(file_id)
    
    if status is None:
        raise HTTPException(status_code=404, detail=f"No status found for file_id: {file_id[:16]}...")
    
    return status


@app.delete("/download/{file_id}")
async def cancel_download(file_id: str) -> dict:
    """
    Cancel an in-progress download.
    
    Cancels the async task and cleans up resources.
    
    Args:
        file_id: The file ID to cancel
        
    Returns:
        200: { "status": "cancelled", "file_id": "..." }
        404: No active download found for file_id
    """
    if not file_id or not file_id.strip():
        raise HTTPException(status_code=400, detail="file_id cannot be empty")
    
    file_id = file_id.strip()
    
    logger.info("Cancel requested: %s", file_id[:16] if len(file_id) > 16 else file_id)
    
    # Check if there's an active task
    if file_id not in active_tasks:
        raise HTTPException(
            status_code=404,
            detail=f"No active download found for file_id: {file_id[:16]}..."
        )
    
    task = active_tasks[file_id]
    
    if task.done():
        # Task already finished, just clean up reference
        del active_tasks[file_id]
        return {"status": "already_completed", "file_id": file_id}
    
    # Cancel the task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    
    # Clean up reference
    if file_id in active_tasks:
        del active_tasks[file_id]
    
    logger.info("Download cancelled: %s", file_id[:16])
    
    return {"status": "cancelled", "file_id": file_id}


@app.get("/downloads")
async def list_downloads() -> dict:
    """
    List all active and incomplete downloads.
    
    Returns:
        200: {
            "active": [...],      # Currently running downloads
            "incomplete": [...]   # Paused/incomplete downloads (can be resumed)
        }
    """
    global peer_node
    
    if peer_node is None:
        raise HTTPException(status_code=500, detail="PeerNode not initialized")
    
    # Get active downloads (tasks currently running)
    active = []
    for file_id, task in list(active_tasks.items()):
        if not task.done():
            # Try to get status for this download
            status = await peer_node.get_status(file_id)
            if status:
                active.append(status)
            else:
                active.append({
                    "file_id": file_id,
                    "status": "running",
                    "percent": 0.0
                })
    
    # Get incomplete downloads from progress tracker
    from peer.progress_tracker import ProgressTracker
    progress_tracker = ProgressTracker(default_config)
    incomplete = progress_tracker.list_incomplete_downloads()
    
    # Filter out downloads that are currently active
    active_ids = set(active_tasks.keys())
    incomplete = [d for d in incomplete if d.get("file_id") not in active_ids]
    
    return {
        "active": active,
        "incomplete": incomplete,
        "total_active": len(active),
        "total_incomplete": len(incomplete)
    }


@app.get("/peers/{file_id}")
async def get_peers(file_id: str) -> dict:
    """
    Get peer list for a file from the tracker.
    
    Args:
        file_id: The file ID to get peers for
        
    Returns:
        200: { "file_id": "...", "peers": [...], "chunk_count": N }
        404: File not found on tracker
    """
    if not file_id or not file_id.strip():
        raise HTTPException(status_code=400, detail="file_id cannot be empty")
    
    file_id = file_id.strip()
    
    logger.info("Peers requested: %s", file_id[:16] if len(file_id) > 16 else file_id)
    
    try:
        # Connect to tracker and get peer info
        tracker_client = TrackerClient(
            host=default_config.tracker_host,
            port=default_config.tracker_port
        )
        
        # Get file metadata from tracker
        file_info = await tracker_client.get_file(file_id)
        
        if not file_info:
            raise HTTPException(
                status_code=404,
                detail=f"File not found on tracker: {file_id[:16]}..."
            )
        
        # Get peers for each chunk
        peers_response = await tracker_client.get_peers(file_id)
        
        return {
            "file_id": file_id,
            "filename": file_info.get("filename", "unknown"),
            "total_chunks": file_info.get("total_chunks", 0),
            "file_size": file_info.get("file_size", 0),
            "peers": peers_response.get("peers", {}),
            "peer_count": len(set(
                f"{p['host']}:{p['port']}"
                for chunk_peers in peers_response.get("peers", {}).values()
                for p in chunk_peers
            ))
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting peers for %s: %s", file_id[:16], e)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# SECTION 6: Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    
    print("P2P File Share REST API")
    print("=" * 50)
    print(f"Tracker: {default_config.tracker_host}:{default_config.tracker_port}")
    print(f"Peer Port: {default_config.peer_port}")
    print(f"Download Dir: {default_config.download_dir}")
    print("=" * 50)
    
    uvicorn.run(
        "api.rest_api:app",
        host="0.0.0.0",
        port=8080,
        reload=True
    )
