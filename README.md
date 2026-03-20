# P2P File Sharing System

A production-quality Peer-to-Peer file sharing system in Python, similar to BitTorrent.
Designed for portability to Android.

## Features

- **Chunk-based file transfer**: Files split into 1MB chunks for parallel downloading
- **SHA-256 verification**: Every chunk is hash-verified for integrity
- **Resume support**: Interrupted downloads can be resumed
- **Parallel downloads**: Download from multiple peers simultaneously
- **Lightweight tracker**: Simple TCP server for peer discovery

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Peer A    │────▶│   Tracker   │◀────│   Peer B    │
│  (Seeder)   │     │   Server    │     │ (Leecher)   │
└─────────────┘     └─────────────┘     └─────────────┘
       │                                       │
       └───────────────────────────────────────┘
              Direct chunk transfer (P2P)
```

## Project Structure

```
p2p_fileshare/
├── tracker/              # Tracker server (peer discovery)
│   ├── tracker_server.py # TCP server for peer registration
│   └── tracker_store.py  # In-memory peer database
├── peer/                 # Peer node components
│   ├── peer_node.py      # Main orchestration engine
│   ├── chunk_handler.py  # File splitting/reassembly
│   ├── downloader.py     # Parallel chunk downloader
│   ├── uploader.py       # Chunk serving TCP server
│   └── progress_tracker.py # Download state persistence
├── utils/                # Shared utilities
│   ├── config.py         # Configuration management
│   ├── hashing.py        # SHA-256 operations
│   └── logger.py         # Structured logging
├── cli/                  # Command-line interface
│   └── main.py           # CLI entry point
├── api/                  # REST API (scaffold, for Android)
│   └── rest_api.py       # FastAPI wrapper
└── tests/                # Unit tests
```

## Installation

```bash
# Clone the repository
cd p2p_fileshare

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt
```

## Quick Start

### Start the Tracker Server
```bash
python -m cli.main tracker
# Tracker running on 127.0.0.1:8000
```

### Share a File
```bash
python -m cli.main upload --file path/to/myfile.zip
# Output: File ID: abc123def456...
```

### Download a File
```bash
python -m cli.main download --id abc123def456...
# Progress: [████████░░] 80% | 40/50 chunks | 2.3 MB/s
```

### Check Status
```bash
python -m cli.main status --id abc123def456...
```

## Configuration

Configuration via environment variables or `Config` object:

| Variable | Default | Description |
|----------|---------|-------------|
| `P2P_CHUNK_SIZE` | 1048576 | Chunk size in bytes (1MB) |
| `P2P_TRACKER_HOST` | 127.0.0.1 | Tracker server host |
| `P2P_TRACKER_PORT` | 8000 | Tracker server port |
| `P2P_PEER_PORT` | 8001 | Peer listening port |
| `P2P_DOWNLOAD_DIR` | ./downloads | Downloaded files directory |
| `P2P_CHUNKS_DIR` | ./chunks | Chunk storage directory |
| `P2P_MAX_CONCURRENT` | 5 | Max parallel downloads |

## Wire Protocol

All network communication uses newline-delimited JSON over TCP.

### Tracker Commands

```json
// Register file metadata
{"cmd": "REGISTER_FILE", "file_id": "...", "filename": "...", "total_chunks": 10, "chunk_hashes": ["..."]}

// Register peer as having a chunk
{"cmd": "REGISTER", "file_id": "...", "chunk_index": 0, "peer_host": "1.2.3.4", "peer_port": 8001}

// Get peers for a file
{"cmd": "GET_PEERS", "file_id": "..."}
// Response: {"status": "ok", "peers": {0: [["1.2.3.4", 8001]], ...}, "metadata": {...}}
```

### Peer Commands

```json
// Request a chunk
{"cmd": "GET_CHUNK", "file_id": "...", "chunk_index": 0}
// Response: {"status": "ok", "chunk_index": 0, "data": "<base64>", "hash": "..."}
```

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python tests/test_hashing.py
python tests/test_chunking.py
```

## Android Compatibility

This project is designed for Android portability:

- ✅ No OS-specific paths (all paths are injectable)
- ✅ No `os.fork()` or platform syscalls
- ✅ Pure Python (no native extensions)
- ✅ Asyncio-based (compatible with Android's event loops)
- ✅ Progress callbacks for UI integration
- ✅ Resume support (critical for mobile)

### Android Integration Options

**Option A: Python on Android (Chaquopy)**
- Wrap `peer_node.py` in FastAPI
- Run as foreground service
- Flutter/Kotlin UI calls REST endpoints

**Option B: Kotlin Rewrite**
- Same wire protocol = interoperable
- Rewrite core in Kotlin coroutines
- Native performance

## Development Status

- [x] utils/config.py - Configuration management
- [x] utils/hashing.py - SHA-256 operations
- [x] utils/logger.py - Structured logging
- [x] peer/chunk_handler.py - File chunking
- [x] api/rest_api.py - Scaffold only
- [x] tests/test_hashing.py - Hashing tests
- [x] tests/test_chunking.py - Chunking tests
- [ ] tracker/tracker_store.py
- [ ] tracker/tracker_server.py
- [ ] peer/uploader.py
- [ ] peer/downloader.py
- [ ] peer/progress_tracker.py
- [ ] peer/peer_node.py
- [ ] cli/main.py

## License

MIT License

## Contributing

This is a portfolio project demonstrating:
- Networking & distributed systems
- Asyncio concurrency
- Clean architecture
- Android-portable design
