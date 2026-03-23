#!/bin/bash
# ==============================================================================
# End-to-End Test for P2P File Sharing System
# ==============================================================================
# This script verifies the complete workflow:
# 1. Create a test file
# 2. Start the tracker server
# 3. Upload the file (split into chunks, register with tracker)
# 4. Download the file using the file_id
# 5. Verify file integrity via SHA256 hash comparison
# 6. Run existing unit tests
# 7. Clean up
# ==============================================================================

set -e  # Exit on any error

# Fix for Windows Unicode issues with Rich library
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test configuration
TEST_FILE="test_input.bin"
TEST_OUTPUT_DIR="./test_output"
TEST_FILE_SIZE_MB=5
TRACKER_STARTUP_WAIT=3
UPLOAD_STARTUP_WAIT=3

# Cleanup function
cleanup() {
    echo -e "\n${YELLOW}=== Cleanup ===${NC}"
    
    # Kill background processes if they exist
    if [ ! -z "$TRACKER_PID" ] && kill -0 $TRACKER_PID 2>/dev/null; then
        echo "Stopping tracker (PID: $TRACKER_PID)..."
        kill $TRACKER_PID 2>/dev/null || true
        wait $TRACKER_PID 2>/dev/null || true
    fi
    
    if [ ! -z "$UPLOAD_PID" ] && kill -0 $UPLOAD_PID 2>/dev/null; then
        echo "Stopping uploader (PID: $UPLOAD_PID)..."
        kill $UPLOAD_PID 2>/dev/null || true
        wait $UPLOAD_PID 2>/dev/null || true
    fi
    
    # Remove test files and directories
    rm -f "$TEST_FILE"
    rm -rf "$TEST_OUTPUT_DIR"
    rm -rf ./chunks
    rm -rf ./progress
    rm -rf ./downloads/test_input.bin
    
    echo "Cleanup complete"
}

# Set trap to cleanup on exit (success or failure)
trap cleanup EXIT

# ==============================================================================
# Test Start
# ==============================================================================

echo "=============================================="
echo "P2P File Sharing System - End-to-End Test"
echo "=============================================="
echo ""

# ==============================================================================
# Step 1: Create test file
# ==============================================================================
echo -e "${YELLOW}=== Step 1: Creating ${TEST_FILE_SIZE_MB}MB test file ===${NC}"

# Create random binary file
dd if=/dev/urandom of="$TEST_FILE" bs=1M count=$TEST_FILE_SIZE_MB 2>/dev/null

if [ ! -f "$TEST_FILE" ]; then
    echo -e "${RED}FAIL: Could not create test file${NC}"
    exit 1
fi

ORIGINAL_SIZE=$(stat -f%z "$TEST_FILE" 2>/dev/null || stat -c%s "$TEST_FILE" 2>/dev/null)
echo "Created test file: $TEST_FILE ($ORIGINAL_SIZE bytes)"
echo -e "${GREEN}✓ Test file created${NC}"
echo ""

# ==============================================================================
# Step 2: Start tracker server
# ==============================================================================
echo -e "${YELLOW}=== Step 2: Starting tracker server ===${NC}"

# Start tracker in background
python -m cli.main tracker &
TRACKER_PID=$!

echo "Tracker PID: $TRACKER_PID"
echo "Waiting ${TRACKER_STARTUP_WAIT}s for tracker to start..."
sleep $TRACKER_STARTUP_WAIT

# Verify tracker is running
if ! kill -0 $TRACKER_PID 2>/dev/null; then
    echo -e "${RED}FAIL: Tracker failed to start${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Tracker running on 127.0.0.1:8000${NC}"
echo ""

# ==============================================================================
# Step 3: Upload file
# ==============================================================================
echo -e "${YELLOW}=== Step 3: Uploading file ===${NC}"

# Run upload in background and capture output
UPLOAD_OUTPUT_FILE=$(mktemp)
python -m cli.main upload --file "$TEST_FILE" > "$UPLOAD_OUTPUT_FILE" 2>&1 &
UPLOAD_PID=$!

echo "Upload PID: $UPLOAD_PID"
echo "Waiting ${UPLOAD_STARTUP_WAIT}s for upload to complete..."
sleep $UPLOAD_STARTUP_WAIT

# Read and display upload output
UPLOAD_OUTPUT=$(cat "$UPLOAD_OUTPUT_FILE")
echo "Upload output:"
echo "$UPLOAD_OUTPUT"

# Extract file_id from output
# The logs show "File ID: ae85ae9b915c25e8" (truncated) but we need full ID
# Look for the full 64-char hash or the truncated version in logs

# First try: Look for full 64-char hash
FILE_ID=$(echo "$UPLOAD_OUTPUT" | grep -oE '[a-f0-9]{64}' | head -1)

# Second try: Extract from "File ID: <hash>" pattern (may be truncated in display)
if [ -z "$FILE_ID" ]; then
    # Get the file_id from the peer.node log line which shows first 16 chars
    PARTIAL_ID=$(echo "$UPLOAD_OUTPUT" | grep "File ID:" | grep -oE '[a-f0-9]{16}' | head -1)
    if [ ! -z "$PARTIAL_ID" ]; then
        # The tracker store also logs the file - try to find full ID from chunks directory
        CHUNK_DIR=$(ls -d chunks/*/ 2>/dev/null | head -1)
        if [ ! -z "$CHUNK_DIR" ]; then
            FILE_ID=$(basename "$CHUNK_DIR")
        fi
    fi
fi

# Third try: Just use the directory name from chunks folder
if [ -z "$FILE_ID" ]; then
    FILE_ID=$(ls chunks/ 2>/dev/null | head -1)
fi

rm -f "$UPLOAD_OUTPUT_FILE"

if [ -z "$FILE_ID" ]; then
    echo -e "${RED}FAIL: Could not extract file_id from upload output${NC}"
    echo "Full output was:"
    echo "$UPLOAD_OUTPUT"
    exit 1
fi

echo ""
echo "Captured File ID: $FILE_ID"
echo -e "${GREEN}✓ Upload successful${NC}"
echo ""

# ==============================================================================
# Step 4: Download file
# ==============================================================================
echo -e "${YELLOW}=== Step 4: Downloading file ===${NC}"

# Create output directory
mkdir -p "$TEST_OUTPUT_DIR"

# Run download with custom output directory
P2P_DOWNLOAD_DIR="$TEST_OUTPUT_DIR" python -m cli.main download --id "$FILE_ID" --output "$TEST_OUTPUT_DIR"

# Find downloaded file
DOWNLOADED_FILE=$(find "$TEST_OUTPUT_DIR" -type f | head -1)

if [ -z "$DOWNLOADED_FILE" ] || [ ! -f "$DOWNLOADED_FILE" ]; then
    echo -e "${RED}FAIL: Downloaded file not found in $TEST_OUTPUT_DIR${NC}"
    ls -la "$TEST_OUTPUT_DIR" 2>/dev/null || echo "Directory does not exist"
    exit 1
fi

DOWNLOADED_SIZE=$(stat -f%z "$DOWNLOADED_FILE" 2>/dev/null || stat -c%s "$DOWNLOADED_FILE" 2>/dev/null)
echo "Downloaded file: $DOWNLOADED_FILE ($DOWNLOADED_SIZE bytes)"
echo -e "${GREEN}✓ Download successful${NC}"
echo ""

# ==============================================================================
# Step 5: Verify integrity
# ==============================================================================
echo -e "${YELLOW}=== Step 5: Verifying file integrity ===${NC}"

# Compute SHA256 hashes
ORIGINAL_HASH=$(sha256sum "$TEST_FILE" | awk '{print $1}')
DOWNLOADED_HASH=$(sha256sum "$DOWNLOADED_FILE" | awk '{print $1}')

echo "Original file hash:   $ORIGINAL_HASH"
echo "Downloaded file hash: $DOWNLOADED_HASH"
echo ""

if [ "$ORIGINAL_HASH" = "$DOWNLOADED_HASH" ]; then
    echo -e "${GREEN}✓ PASS: File integrity verified - hashes match perfectly${NC}"
else
    echo -e "${RED}✗ FAIL: Hash mismatch - file corrupted during transfer${NC}"
    echo ""
    echo "Original size:   $ORIGINAL_SIZE bytes"
    echo "Downloaded size: $DOWNLOADED_SIZE bytes"
    exit 1
fi
echo ""

# ==============================================================================
# Step 6: Run unit tests
# ==============================================================================
echo -e "${YELLOW}=== Step 6: Running unit tests ===${NC}"

# Stop the uploader before running tests to free ports
if [ ! -z "$UPLOAD_PID" ] && kill -0 $UPLOAD_PID 2>/dev/null; then
    kill $UPLOAD_PID 2>/dev/null || true
    wait $UPLOAD_PID 2>/dev/null || true
    UPLOAD_PID=""
fi

# Run pytest
python -m pytest tests/ -v --tb=short

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ All unit tests passed${NC}"
else
    echo -e "${RED}✗ Some unit tests failed${NC}"
    exit 1
fi
echo ""

# ==============================================================================
# Final Summary
# ==============================================================================
echo "=============================================="
echo -e "${GREEN}=== ALL TESTS PASSED ===${NC}"
echo "=============================================="
echo ""
echo "Test Summary:"
echo "  ✓ Created ${TEST_FILE_SIZE_MB}MB test file"
echo "  ✓ Tracker server started and running"
echo "  ✓ File uploaded and registered"
echo "  ✓ File downloaded from peer"
echo "  ✓ File integrity verified (SHA256 match)"
echo "  ✓ Unit tests passed"
echo ""
echo "The P2P file sharing system is working correctly!"
