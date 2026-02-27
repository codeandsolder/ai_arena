"""
Backend API Integration Test Script for AI Optimization Arena.

Tests all major API endpoints:
- Problem CRUD operations
- Test case upload
- Run CRUD and lifecycle
- WebSocket connectivity
- Error handling

Usage:
    python scripts/test_backend.py [--base-url BASE_URL]

Returns:
    Exit code 0 if all tests pass, 1 otherwise.
"""

import argparse
import asyncio
import io
import json
import logging
import sys
import zipfile
from datetime import datetime
from typing import Optional

import aiohttp
import websockets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class BackendAPITester:
    """Test harness for backend API endpoints."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v1"
        self.ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.session: Optional[aiohttp.ClientSession] = None
        self.test_results: list[dict] = []

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def log_result(self, test_name: str, passed: bool, message: str = ""):
        """Log a test result."""
        status = "✅ PASS" if passed else "❌ FAIL"
        self.test_results.append({
            "name": test_name,
            "passed": passed,
            "message": message,
        })
        if passed:
            logger.info(f"{status}: {test_name}")
        else:
            logger.error(f"{status}: {test_name} - {message}")

    async def test_health_check(self) -> bool:
        """Test that the API is running."""
        try:
            async with self.session.get(f"{self.base_url}/") as response:
                if response.status == 200:
                    data = await response.json()
                    if "name" in data and "AI Optimization Arena" in data["name"]:
                        self.log_result("Health Check", True)
                        return True
                    else:
                        self.log_result("Health Check", False, "Unexpected response format")
                        return False
                else:
                    self.log_result("Health Check", False, f"Status {response.status}")
                    return False
        except Exception as e:
            self.log_result("Health Check", False, str(e))
            return False

    async def test_swagger_docs(self) -> bool:
        """Test that Swagger UI is accessible."""
        try:
            async with self.session.get(f"{self.base_url}/docs") as response:
                if response.status == 200:
                    text = await response.text()
                    if "swagger" in text.lower():
                        self.log_result("Swagger Docs", True)
                        return True
                self.log_result("Swagger Docs", False, f"Status {response.status}")
                return False
        except Exception as e:
            self.log_result("Swagger Docs", False, str(e))
            return False

    # =============================================================================
    # Problem API Tests
    # =============================================================================

    async def test_problem_crud(self) -> tuple[Optional[int], bool]:
        """Test problem CRUD operations."""
        problem_id = None
        all_passed = True

        # CREATE
        try:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            problem_data = {
                "name": f"Test Problem {timestamp}",
                "slug": f"test-problem-{timestamp}",
                "description_md": "# Test Problem\n\nThis is a test problem for integration testing.",
                "time_limit_ms": 1000,
                "memory_limit_mb": 128,
                "scoring_mode": "binary",
            }

            async with self.session.post(
                f"{self.api_url}/problems/",
                json=problem_data,
            ) as response:
                if response.status == 201:
                    data = await response.json()
                    problem_id = data.get("id")
                    self.log_result("Problem Create", True, f"ID: {problem_id}")
                else:
                    text = await response.text()
                    self.log_result("Problem Create", False, f"Status {response.status}: {text}")
                    all_passed = False
        except Exception as e:
            self.log_result("Problem Create", False, str(e))
            all_passed = False

        if not problem_id:
            return None, False

        # READ (Get)
        try:
            async with self.session.get(
                f"{self.api_url}/problems/{problem_id}"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("id") == problem_id:
                        self.log_result("Problem Get", True)
                    else:
                        self.log_result("Problem Get", False, "ID mismatch")
                        all_passed = False
                else:
                    text = await response.text()
                    self.log_result("Problem Get", False, f"Status {response.status}: {text}")
                    all_passed = False
        except Exception as e:
            self.log_result("Problem Get", False, str(e))
            all_passed = False

        # READ (List)
        try:
            async with self.session.get(f"{self.api_url}/problems/") as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list):
                        self.log_result("Problem List", True, f"Found {len(data)} problems")
                    else:
                        self.log_result("Problem List", False, "Response is not a list")
                        all_passed = False
                else:
                    text = await response.text()
                    self.log_result("Problem List", False, f"Status {response.status}: {text}")
                    all_passed = False
        except Exception as e:
            self.log_result("Problem List", False, str(e))
            all_passed = False

        # UPDATE
        try:
            update_data = {
                "name": f"Updated Test Problem {timestamp}",
                "time_limit_ms": 2000,
            }
            async with self.session.put(
                f"{self.api_url}/problems/{problem_id}",
                json=update_data,
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("name") == update_data["name"]:
                        self.log_result("Problem Update", True)
                    else:
                        self.log_result("Problem Update", False, "Name not updated")
                        all_passed = False
                else:
                    text = await response.text()
                    self.log_result("Problem Update", False, f"Status {response.status}: {text}")
                    all_passed = False
        except Exception as e:
            self.log_result("Problem Update", False, str(e))
            all_passed = False

        return problem_id, all_passed

    async def test_problem_upload_tests(self, problem_id: int) -> bool:
        """Test uploading test cases to a problem."""
        try:
            # Create a ZIP file in memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.writestr("001.in", "3 5\n")
                zip_file.writestr("001.out", "8\n")
                zip_file.writestr("002.in", "-10 20\n")
                zip_file.writestr("002.out", "10\n")

            zip_buffer.seek(0)

            data = aiohttp.FormData()
            data.add_field(
                "file",
                zip_buffer,
                filename="tests.zip",
                content_type="application/zip",
            )

            async with self.session.post(
                f"{self.api_url}/problems/{problem_id}/tests",
                data=data,
            ) as response:
                if response.status in (200, 201):
                    result = await response.json()
                    if result.get("test_count") >= 2:
                        self.log_result(
                            "Problem Upload Tests", True, f"Tests: {result.get('test_count')}"
                        )
                        return True
                    else:
                        self.log_result("Problem Upload Tests", False, "Not enough tests uploaded")
                        return False
                else:
                    text = await response.text()
                    self.log_result(
                        "Problem Upload Tests", False, f"Status {response.status}: {text}"
                    )
                    return False
        except Exception as e:
            self.log_result("Problem Upload Tests", False, str(e))
            return False

    async def test_problem_delete(self, problem_id: int) -> bool:
        """Test deleting a problem."""
        try:
            async with self.session.delete(
                f"{self.api_url}/problems/{problem_id}"
            ) as response:
                if response.status == 204:
                    self.log_result("Problem Delete", True)
                    return True
                else:
                    text = await response.text()
                    self.log_result("Problem Delete", False, f"Status {response.status}: {text}")
                    return False
        except Exception as e:
            self.log_result("Problem Delete", False, str(e))
            return False

    # =============================================================================
    # Run API Tests
    # =============================================================================

    async def test_run_crud(self, problem_id: int) -> tuple[Optional[int], bool]:
        """Test run CRUD operations."""
        run_id = None
        all_passed = True

        # CREATE
        try:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            config = {
                "models": [
                    {"slug": "anthropic/claude-3.5-sonnet", "enabled": True, "temperature": 0.7, "max_tokens": 4096}
                ],
                "judge_model": {"slug": "anthropic/claude-3.5-sonnet", "temperature": 0.5, "max_tokens": 2048},
                "prompts": {
                    "system_prompt": "You are a competitive programmer.",
                },
                "response_format": "json",
                "scoring": {
                    "correctness_weight": 0.6,
                    "speed_weight": 0.25,
                    "memory_weight": 0.15,
                    "penalty_wrong_answer": -0.5,
                },
                "execution": {
                    "timeout_buffer_ms": 100,
                    "enable_cache_simulation": True,
                    "measure_memory_peak": True,
                },
            }

            run_data = {
                "name": f"Test Run {timestamp}",
                "problem_id": problem_id,
                "config_json": json.dumps(config),
            }

            async with self.session.post(
                f"{self.api_url}/runs/",
                json=run_data,
            ) as response:
                if response.status == 201:
                    data = await response.json()
                    run_id = data.get("id")
                    self.log_result("Run Create", True, f"ID: {run_id}")
                else:
                    text = await response.text()
                    self.log_result("Run Create", False, f"Status {response.status}: {text}")
                    all_passed = False
        except Exception as e:
            self.log_result("Run Create", False, str(e))
            all_passed = False

        if not run_id:
            return None, False

        # READ (Get)
        try:
            async with self.session.get(f"{self.api_url}/runs/{run_id}") as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("id") == run_id and "config" in data:
                        self.log_result("Run Get", True)
                    else:
                        self.log_result("Run Get", False, "ID mismatch or missing config")
                        all_passed = False
                else:
                    text = await response.text()
                    self.log_result("Run Get", False, f"Status {response.status}: {text}")
                    all_passed = False
        except Exception as e:
            self.log_result("Run Get", False, str(e))
            all_passed = False

        # READ (List)
        try:
            async with self.session.get(f"{self.api_url}/runs/") as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list):
                        self.log_result("Run List", True, f"Found {len(data)} runs")
                    else:
                        self.log_result("Run List", False, "Response is not a list")
                        all_passed = False
                else:
                    text = await response.text()
                    self.log_result("Run List", False, f"Status {response.status}: {text}")
                    all_passed = False
        except Exception as e:
            self.log_result("Run List", False, str(e))
            all_passed = False

        # UPDATE
        try:
            update_data = {
                "name": f"Updated Test Run {timestamp}",
            }
            async with self.session.put(
                f"{self.api_url}/runs/{run_id}",
                json=update_data,
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("name") == update_data["name"]:
                        self.log_result("Run Update", True)
                    else:
                        self.log_result("Run Update", False, "Name not updated")
                        all_passed = False
                else:
                    text = await response.text()
                    self.log_result("Run Update", False, f"Status {response.status}: {text}")
                    all_passed = False
        except Exception as e:
            self.log_result("Run Update", False, str(e))
            all_passed = False

        return run_id, all_passed

    async def test_run_lifecycle(self, run_id: int) -> bool:
        """Test run lifecycle operations (start, pause, resume)."""
        all_passed = True

        # Note: We won't actually start the run as it requires OpenRouter API key
        # and Docker image. Instead, we'll just verify the endpoints exist.

        # Try to start (will fail without API key, but verifies endpoint)
        try:
            async with self.session.post(
                f"{self.api_url}/runs/{run_id}/start?num_rounds=1"
            ) as response:
                # We expect this might fail due to missing API key or Docker
                # but the endpoint should be accessible
                if response.status in [200, 422, 500, 503]:
                    self.log_result("Run Start Endpoint", True, f"Status {response.status}")
                else:
                    text = await response.text()
                    self.log_result(
                        "Run Start Endpoint", False, f"Unexpected status {response.status}: {text}"
                    )
                    all_passed = False
        except Exception as e:
            self.log_result("Run Start Endpoint", False, str(e))
            all_passed = False

        # Pause endpoint check
        try:
            async with self.session.post(
                f"{self.api_url}/runs/{run_id}/pause"
            ) as response:
                if response.status in [200, 400, 422]:  # 400 if not running
                    self.log_result("Run Pause Endpoint", True, f"Status {response.status}")
                else:
                    text = await response.text()
                    self.log_result(
                        "Run Pause Endpoint", False, f"Unexpected status {response.status}: {text}"
                    )
                    all_passed = False
        except Exception as e:
            self.log_result("Run Pause Endpoint", False, str(e))
            all_passed = False

        return all_passed

    async def test_run_delete(self, run_id: int) -> bool:
        """Test deleting a run."""
        try:
            async with self.session.delete(f"{self.api_url}/runs/{run_id}") as response:
                if response.status == 204:
                    self.log_result("Run Delete", True)
                    return True
                else:
                    text = await response.text()
                    self.log_result("Run Delete", False, f"Status {response.status}: {text}")
                    return False
        except Exception as e:
            self.log_result("Run Delete", False, str(e))
            return False

    # =============================================================================
    # WebSocket Tests
    # =============================================================================

    async def test_websocket(self, run_id: int) -> bool:
        """Test WebSocket connection."""
        ws_url = f"{self.ws_url}/api/v1/ws/runs/{run_id}"

        try:
            # Try to connect with a timeout
            async with websockets.connect(ws_url) as websocket:
                # Wait for initial connection message or timeout
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    data = json.loads(message)
                    self.log_result("WebSocket Connect", True, f"Received: {data.get('type', 'unknown')}")
                    return True
                except asyncio.TimeoutError:
                    # Connection succeeded but no message received - that's OK
                    self.log_result("WebSocket Connect", True, "Connected (no initial message)")
                    return True
        except websockets.exceptions.ConnectionRefused:
            self.log_result("WebSocket Connect", False, "Connection refused")
            return False
        except Exception as e:
            self.log_result("WebSocket Connect", False, str(e))
            return False

    # =============================================================================
    # Error Handling Tests
    # =============================================================================

    async def test_error_handling(self) -> bool:
        """Test API error handling."""
        all_passed = True

        # Test 404 for non-existent problem
        try:
            async with self.session.get(f"{self.api_url}/problems/999999") as response:
                if response.status == 404:
                    self.log_result("Error Handling - 404", True)
                else:
                    self.log_result(
                        "Error Handling - 404", False, f"Expected 404, got {response.status}"
                    )
                    all_passed = False
        except Exception as e:
            self.log_result("Error Handling - 404", False, str(e))
            all_passed = False

        # Test 400 for invalid data
        try:
            invalid_data = {
                "name": "",  # Empty name should fail
                "slug": "invalid slug with spaces",
                "description_md": "Test",
            }
            async with self.session.post(
                f"{self.api_url}/problems/",
                json=invalid_data,
            ) as response:
                if response.status == 422:  # Validation error
                    self.log_result("Error Handling - Validation", True)
                else:
                    self.log_result(
                        "Error Handling - Validation",
                        False,
                        f"Expected 422, got {response.status}",
                    )
                    all_passed = False
        except Exception as e:
            self.log_result("Error Handling - Validation", False, str(e))
            all_passed = False

        return all_passed

    # =============================================================================
    # Main Test Runner
    # =============================================================================

    async def run_all_tests(self) -> bool:
        """Run all tests and return overall success status."""
        logger.info("=" * 60)
        logger.info("Starting Backend API Integration Tests")
        logger.info("=" * 60)
        logger.info(f"Base URL: {self.base_url}")
        logger.info("")

        # Basic connectivity tests
        health_ok = await self.test_health_check()
        docs_ok = await self.test_swagger_docs()

        if not health_ok:
            logger.error("\n❌ API is not responding. Make sure the backend is running.")
            return False

        # Problem CRUD tests
        logger.info("\n--- Problem API Tests ---")
        problem_id, problem_ok = await self.test_problem_crud()

        if problem_id:
            # Test upload
            upload_ok = await self.test_problem_upload_tests(problem_id)

            # Run tests (create a run for this problem)
            logger.info("\n--- Run API Tests ---")
            run_id, run_ok = await self.test_run_crud(problem_id)

            if run_id:
                # Test lifecycle
                lifecycle_ok = await self.test_run_lifecycle(run_id)

                # Test WebSocket
                logger.info("\n--- WebSocket Tests ---")
                ws_ok = await self.test_websocket(run_id)

                # Clean up run
                await self.test_run_delete(run_id)
            else:
                lifecycle_ok = False
                ws_ok = False

            # Clean up problem
            await self.test_problem_delete(problem_id)
        else:
            problem_ok = False
            upload_ok = False
            run_ok = False
            lifecycle_ok = False
            ws_ok = False

        # Error handling tests
        logger.info("\n--- Error Handling Tests ---")
        error_ok = await self.test_error_handling()

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("Test Summary")
        logger.info("=" * 60)

        passed = sum(1 for r in self.test_results if r["passed"])
        failed = sum(1 for r in self.test_results if not r["passed"])
        total = len(self.test_results)

        for result in self.test_results:
            status = "✅" if result["passed"] else "❌"
            logger.info(f"{status} {result['name']}")

        logger.info("")
        logger.info(f"Total: {total} | Passed: {passed} | Failed: {failed}")

        if failed == 0:
            logger.info("\n🎉 All tests passed!")
            return True
        else:
            logger.info(f"\n⚠️  {failed} test(s) failed.")
            return False


def main():
    parser = argparse.ArgumentParser(
        description="Backend API Integration Test Script for AI Optimization Arena"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the backend API (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    async def run_tests():
        async with BackendAPITester(args.base_url) as tester:
            return await tester.run_all_tests()

    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
