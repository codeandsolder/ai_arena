"""
WebSocket manager for real-time status broadcasting.

This module provides a simple WebSocket manager that maintains connections
per run and broadcasts status updates during orchestration.
"""

import json
import logging
from typing import Dict, Set, Any, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connections for real-time status updates.
    
    Maintains a mapping of run_id to connected WebSocket clients,
    allowing broadcasts to all clients watching a specific run.
    """
    
    def __init__(self):
        """Initialize the WebSocket manager."""
        # Map run_id -> set of WebSocket connections
        self.connections: Dict[int, Set[WebSocket]] = {}
        logger.info("WebSocketManager initialized")
    
    async def connect(self, websocket: WebSocket, run_id: int):
        """
        Accept a new WebSocket connection for a run.
        
        Args:
            websocket: The WebSocket connection to accept
            run_id: The run ID this client is watching
        """
        await websocket.accept()
        
        if run_id not in self.connections:
            self.connections[run_id] = set()
        
        self.connections[run_id].add(websocket)
        logger.info(f"WebSocket connected for run {run_id}. Total connections: {len(self.connections[run_id])}")
    
    def disconnect(self, websocket: WebSocket, run_id: int):
        """
        Remove a WebSocket connection.
        
        Args:
            websocket: The WebSocket connection to remove
            run_id: The run ID this client was watching
        """
        if run_id in self.connections:
            self.connections[run_id].discard(websocket)
            
            # Clean up empty sets
            if not self.connections[run_id]:
                del self.connections[run_id]
            
            logger.info(f"WebSocket disconnected from run {run_id}")
    
    async def broadcast_to_run(self, run_id: int, message: Dict[str, Any]):
        """
        Broadcast a message to all clients watching a specific run.
        
        Args:
            run_id: The run ID to broadcast to
            message: The message to send (will be JSON serialized)
        """
        if run_id not in self.connections:
            return
        
        json_message = json.dumps(message)
        disconnected = set()
        
        for websocket in self.connections[run_id]:
            try:
                await websocket.send_text(json_message)
            except Exception as e:
                logger.warning(f"Failed to send WebSocket message: {e}")
                disconnected.add(websocket)
        
        # Clean up disconnected clients
        for websocket in disconnected:
            self.connections[run_id].discard(websocket)
        
        if disconnected:
            logger.debug(f"Removed {len(disconnected)} disconnected WebSockets from run {run_id}")
    
    async def broadcast_round_status(
        self,
        run_id: int,
        round_id: int,
        status: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Broadcast round status update.
        
        Args:
            run_id: The run ID
            round_id: The round ID
            status: Status string (e.g., "generating", "compiling", "judging")
            details: Optional additional details
        """
        message = {
            "type": "round_status",
            "round_id": round_id,
            "status": status
        }
        if details:
            message.update(details)
        
        await self.broadcast_to_run(run_id, message)
        logger.debug(f"Broadcast round_status for run {run_id}, round {round_id}: {status}")
    
    async def broadcast_solution_status(
        self,
        run_id: int,
        solution_id: int,
        model: str,
        status: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Broadcast solution status update.
        
        Args:
            run_id: The run ID
            solution_id: The solution ID
            model: Model name/slug
            status: Status string (e.g., "generating", "compiling", "benchmarking")
            details: Optional additional details
        """
        message = {
            "type": "solution_status",
            "solution_id": solution_id,
            "model": model,
            "status": status
        }
        if details:
            message.update(details)
        
        await self.broadcast_to_run(run_id, message)
        logger.debug(f"Broadcast solution_status for solution {solution_id}: {status}")
    
    async def broadcast_test_result(
        self,
        run_id: int,
        solution_id: int,
        test_index: int,
        verdict: str,
        time_ms: float,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Broadcast individual test result.
        
        Args:
            run_id: The run ID
            solution_id: The solution ID
            test_index: Test case index
            verdict: Test verdict (e.g., "AC", "WA", "TLE", "RE")
            time_ms: Execution time in milliseconds
            details: Optional additional details
        """
        message = {
            "type": "test_result",
            "solution_id": solution_id,
            "test_index": test_index,
            "verdict": verdict,
            "time_ms": time_ms
        }
        if details:
            message.update(details)
        
        await self.broadcast_to_run(run_id, message)
    
    async def broadcast_round_complete(
        self,
        run_id: int,
        round_id: int,
        scores: Dict[str, Any],
        summary: Optional[str] = None
    ):
        """
        Broadcast round completion.
        
        Args:
            run_id: The run ID
            round_id: The round ID
            scores: Dictionary of solution scores
            summary: Optional round summary text
        """
        message = {
            "type": "round_complete",
            "round_id": round_id,
            "scores": scores
        }
        if summary:
            message["summary"] = summary
        
        await self.broadcast_to_run(run_id, message)
        logger.info(f"Broadcast round_complete for run {run_id}, round {round_id}")
    
    async def broadcast_run_status(
        self,
        run_id: int,
        status: str,
        message: Optional[str] = None
    ):
        """
        Broadcast run-level status update.
        
        Args:
            run_id: The run ID
            status: Run status (e.g., "started", "paused", "resumed", "completed")
            message: Optional status message
        """
        payload = {
            "type": "run_status",
            "run_id": run_id,
            "status": status
        }
        if message:
            payload["message"] = message
        
        await self.broadcast_to_run(run_id, payload)
        logger.info(f"Broadcast run_status for run {run_id}: {status}")
    
    def get_connection_count(self, run_id: Optional[int] = None) -> int:
        """
        Get the number of active WebSocket connections.
        
        Args:
            run_id: If specified, count connections for this run only.
                   If None, count all connections.
        
        Returns:
            Number of active connections
        """
        if run_id is not None:
            return len(self.connections.get(run_id, set()))
        
        return sum(len(conns) for conns in self.connections.values())


# Global WebSocket manager instance
_websocket_manager: Optional[WebSocketManager] = None


def get_websocket_manager() -> WebSocketManager:
    """
    Get or create the global WebSocket manager instance.
    
    Returns:
        WebSocketManager singleton instance
    """
    global _websocket_manager
    if _websocket_manager is None:
        _websocket_manager = WebSocketManager()
    return _websocket_manager


def reset_websocket_manager():
    """Reset the global WebSocket manager (useful for testing)."""
    global _websocket_manager
    _websocket_manager = None
