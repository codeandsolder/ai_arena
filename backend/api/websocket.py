"""
WebSocket endpoint for real-time status updates.

Provides WebSocket connections for streaming run status updates.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from backend.websocket_manager import get_websocket_manager

router = APIRouter(tags=["websocket"])
websocket_router = router  # Alias for consistency
logger = logging.getLogger(__name__)


@router.websocket("/ws/runs/{run_id}")
async def run_status_websocket(websocket: WebSocket, run_id: int):
    """
    WebSocket endpoint for streaming status updates for a run.
    
    Connect to this endpoint to receive real-time updates about:
    - Round status changes (generating, compiling, judging, etc.)
    - Solution status updates
    - Individual test results
    - Round completion with scores
    - Run-level status changes
    
    Message formats:
    
    **Round Status:**
    ```json
    {
        "type": "round_status",
        "round_id": 1,
        "status": "generating"
    }
    ```
    
    **Solution Status:**
    ```json
    {
        "type": "solution_status",
        "solution_id": 5,
        "model": "claude-sonnet-4",
        "status": "compiling"
    }
    ```
    
    **Test Result:**
    ```json
    {
        "type": "test_result",
        "solution_id": 5,
        "test_index": 3,
        "verdict": "AC",
        "time_ms": 12.5
    }
    ```
    
    **Round Complete:**
    ```json
    {
        "type": "round_complete",
        "round_id": 1,
        "scores": {
            "model1": 0.95,
            "model2": 0.87
        }
    }
    ```
    
    **Run Status:**
    ```json
    {
        "type": "run_status",
        "run_id": 1,
        "status": "started"
    }
    ```
    
    Args:
        run_id: The run ID to watch
    """
    manager = get_websocket_manager()
    
    try:
        # Accept the connection
        await manager.connect(websocket, run_id)
        logger.info(f"WebSocket connected for run {run_id}")
        
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "run_id": run_id
        })
        
        # Keep the connection alive and handle client messages
        while True:
            try:
                # Wait for messages from client (optional - allows for ping/pong)
                data = await websocket.receive_text()
                
                # Handle ping messages
                if data == "ping":
                    await websocket.send_text("pong")
                elif data == "status":
                    # Client requesting current status
                    await websocket.send_json({
                        "type": "connection",
                        "status": "connected",
                        "run_id": run_id
                    })
                    
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for run {run_id}")
                break
            except Exception as e:
                logger.warning(f"WebSocket error for run {run_id}: {e}")
                break
                
    except Exception as e:
        logger.error(f"WebSocket error for run {run_id}: {e}")
    finally:
        # Clean up connection
        manager.disconnect(websocket, run_id)
        logger.info(f"WebSocket connection closed for run {run_id}")


@router.get("/ws/status")
async def websocket_status():
    """
    Get current WebSocket connection status.
    
    Returns:
        Status information about active WebSocket connections.
    """
    manager = get_websocket_manager()
    
    return {
        "active_connections": manager.get_connection_count(),
        "monitored_runs": list(manager.connections.keys())
    }