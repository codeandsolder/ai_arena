"""
Docker container lifecycle management for the AI Optimization Arena sandbox system.

Provides secure, isolated execution environment for compiling and running C++ code.
"""

import asyncio
import logging
import tempfile
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from contextlib import asynccontextmanager

try:
    import docker
    from docker.errors import DockerException, NotFound, APIError
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    docker = None

logger = logging.getLogger(__name__)


# Default sandbox configuration
DEFAULT_IMAGE = "arena-sandbox:v2"
DEFAULT_MEMORY_LIMIT = "512m"  # 512 MB
DEFAULT_CPU_PERIOD = 100000     # 100ms
DEFAULT_CPU_QUOTA = 100000      # 100ms = 1 CPU
DEFAULT_TIMEOUT = 300           # 5 minutes


@dataclass
class ContainerResult:
    """Result of a container execution."""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    execution_time_ms: float
    error: Optional[str] = None


class ContainerManager:
    """
    Manages Docker containers for secure code execution.
    
    Provides methods for creating, running, and cleaning up containers
    with strict security constraints.
    """
    
    # Class-level lock for image building to prevent race conditions
    _image_build_lock: asyncio.Lock = None
    
    def __init__(self, image: str = DEFAULT_IMAGE):
        """
        Initialize the container manager.
        
        Args:
            image: Docker image to use for containers
        """
        self.image = image
        self._client: Optional[Any] = None
        
        if not DOCKER_AVAILABLE:
            raise RuntimeError(
                "Docker Python package is not installed. "
                "Install it with: pip install docker"
            )
    
    async def ensure_image_available(self) -> bool:
        """
        Ensure the sandbox image is available locally.
        
        If the image is missing, it will be built from the Dockerfile.
        Uses a class-level lock to prevent race conditions when multiple
        evaluations try to build the same image simultaneously.
        
        Returns:
            True if image is available or was successfully built
        """
        if self.is_image_available(self.image):
            logger.debug(f"Docker image '{self.image}' is already available")
            return True
        
        # Initialize the class-level lock if needed (must be done in async context)
        if ContainerManager._image_build_lock is None:
            ContainerManager._image_build_lock = asyncio.Lock()
        
        # Use lock to prevent concurrent builds of the same image
        async with ContainerManager._image_build_lock:
            # Double-check after acquiring lock (another process may have built it)
            if self.is_image_available(self.image):
                logger.debug(f"Docker image '{self.image}' is already available (after lock)")
                return True
            
            logger.info(f"Docker image '{self.image}' not found. Building...")
            
            # Determine paths
            # Assuming we are in d:/ai_arena/backend/sandbox/container.py
            # Dockerfile is in d:/ai_arena/docker/Dockerfile.sandbox
            # We need the context to be the root or the docker directory
            # If we use d:/ai_arena/docker as context, we need to make sure test_harness.cpp is there
            
            base_dir = Path(__file__).parent.parent.parent
            docker_dir = base_dir / "docker"
            dockerfile = "Dockerfile.sandbox"
            
            if not (docker_dir / dockerfile).exists():
                logger.error(f"Dockerfile not found at {docker_dir / dockerfile}")
                return False
                
            success = await self.build_image(
                dockerfile_path=str(docker_dir),
                dockerfile=dockerfile,
                tag=self.image
            )
            
            if success:
                logger.info(f"Successfully built sandbox image '{self.image}'")
            else:
                logger.error(f"Failed to build sandbox image '{self.image}'")
                
            return success

    @property
    def client(self):
        """Get or create Docker client."""
        if self._client is None:
            try:
                self._client = docker.from_env()
                # Test connection
                self._client.ping()
                logger.info("Docker client initialized successfully")
            except DockerException as e:
                logger.error(f"Failed to initialize Docker client: {e}")
                raise RuntimeError(f"Docker connection failed: {e}")
        return self._client
    
    async def create_container(
        self,
        command: List[str],
        volumes: Optional[Dict[str, Dict[str, str]]] = None,
        mem_limit: str = DEFAULT_MEMORY_LIMIT,
        cpu_period: int = DEFAULT_CPU_PERIOD,
        cpu_quota: int = DEFAULT_CPU_QUOTA,
        network_disabled: bool = True,
        read_only: bool = True,
        tmpfs: Optional[Dict[str, str]] = None,
        working_dir: str = "/workspace",
        user: str = "sandboxuser",
        environment: Optional[Dict[str, str]] = None,
        cap_add: Optional[List[str]] = None
    ) -> Any:
        """
        Create a Docker container with security constraints.
        
        Args:
            command: Command to run in the container
            volumes: Volume mounts {host_path: {"bind": container_path, "mode": "ro|rw"}}
            mem_limit: Memory limit (e.g., "512m", "1g")
            cpu_period: CPU period in microseconds
            cpu_quota: CPU quota in microseconds
            network_disabled: Disable network access
            read_only: Make root filesystem read-only
            tmpfs: Tmpfs mounts {container_path: "size=100m"}
            working_dir: Working directory inside container
            user: User to run as
            environment: Environment variables
            
        Returns:
            Docker container object
        """
        # Prepare volume configuration
        volume_config = volumes or {}
        
        # Default tmpfs for /workspace if not specified
        tmpfs_config = tmpfs or {"/workspace": "size=100m,exec"}
        
        # Prepare container configuration
        container_config = {
            "image": self.image,
            "command": command,
            "volumes": volume_config,
            "mem_limit": mem_limit,
            "cpu_period": cpu_period,
            "cpu_quota": cpu_quota,
            "network_disabled": network_disabled,
            "read_only": read_only,
            "tmpfs": tmpfs_config,
            "working_dir": working_dir,
            "user": user,
            "detach": True,  # Run in background so we can control it
            "stdin_open": False,
            "tty": False,
        }
        
        if environment:
            container_config["environment"] = environment
            
        if cap_add:
            container_config["cap_add"] = cap_add
        
        # Remove None values
        container_config = {k: v for k, v in container_config.items() if v is not None}
        
        logger.debug(f"Creating container with config: {container_config}")
        
        try:
            # Run in thread pool since docker-py is synchronous
            loop = asyncio.get_event_loop()
            
            # Ensure image is available before creating container
            if not self.is_image_available(self.image):
                await self.ensure_image_available()
                
            container = await loop.run_in_executor(
                None,
                lambda: self.client.containers.run(**container_config)
            )
            logger.info(f"Container created: {container.id[:12]}")
            return container
        except DockerException as e:
            logger.error(f"Failed to create container: {e}")
            raise
    
    async def run_container(
        self,
        container: Any,
        timeout: int = DEFAULT_TIMEOUT
    ) -> ContainerResult:
        """
        Wait for container to finish and collect results.
        
        Args:
            container: Docker container object
            timeout: Maximum time to wait in seconds
            
        Returns:
            ContainerResult with execution results
        """
        import time
        start_time = time.time()
        
        try:
            # Wait for container to finish with timeout
            loop = asyncio.get_event_loop()
            
            # Poll for container status with timeout
            while True:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    # Timeout exceeded, kill container
                    logger.warning(
                        f"Container {container.id[:12]} timed out after {timeout}s"
                    )
                    await loop.run_in_executor(None, container.kill, "SIGKILL")
                    await loop.run_in_executor(None, container.wait)
                    return ContainerResult(
                        success=False,
                        stdout="",
                        stderr="",
                        exit_code=-1,
                        execution_time_ms=elapsed * 1000,
                        error=f"Container execution timed out after {timeout} seconds"
                    )
                
                # Check if container has finished
                container.reload()
                if container.status != "running":
                    break
                
                # Wait a bit before checking again
                await asyncio.sleep(0.1)
            
            # Get container results
            end_time = time.time()
            execution_time_ms = (end_time - start_time) * 1000
            
            # Get logs
            try:
                logs = await loop.run_in_executor(
                    None,
                    lambda: container.logs(stdout=True, stderr=True, timestamps=False)
                )
                # Docker logs combine stdout and stderr, try to separate them
                # Unfortunately docker-py doesn't provide separate streams easily
                logs_str = logs.decode("utf-8", errors="replace") if logs else ""
            except Exception as e:
                logger.warning(f"Failed to get container logs: {e}")
                logs_str = ""
            
            # Get exit code
            try:
                container.reload()
                exit_code = container.attrs["State"]["ExitCode"]
            except Exception as e:
                logger.warning(f"Failed to get exit code: {e}")
                exit_code = -1
            
            return ContainerResult(
                success=exit_code == 0,
                stdout=logs_str,
                stderr="",  # Combined in stdout for now
                exit_code=exit_code,
                execution_time_ms=execution_time_ms
            )
            
        except Exception as e:
            logger.error(f"Error running container: {e}")
            return ContainerResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                execution_time_ms=(time.time() - start_time) * 1000,
                error=str(e)
            )
    
    async def cleanup_container(self, container: Any) -> None:
        """
        Remove a container and clean up resources.
        
        Args:
            container: Docker container object
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Force remove the container
            await loop.run_in_executor(
                None,
                lambda: container.remove(force=True, v=True)
            )
            logger.debug(f"Container {container.id[:12]} removed")
        except NotFound:
            logger.debug(f"Container {container.id[:12]} already removed")
        except Exception as e:
            logger.warning(f"Failed to cleanup container: {e}")
    
    async def execute_command(
    self,
    command: List[str],
    volumes: Optional[Dict[str, Dict[str, str]]] = None,
    mem_limit: str = DEFAULT_MEMORY_LIMIT,
    cpu_quota: int = DEFAULT_CPU_QUOTA,
    timeout: int = DEFAULT_TIMEOUT,
    network_disabled: bool = True,
    working_dir: str = "/workspace",
    read_only: bool = True,
    cap_add: Optional[List[str]] = None
) -> ContainerResult:
        """
        Execute a command in a new container and clean up afterwards.
        
        This is a convenience method that creates, runs, and cleans up a container.
        
        Args:
            command: Command to execute
            volumes: Volume mounts
            mem_limit: Memory limit
            cpu_quota: CPU quota
            timeout: Execution timeout
            network_disabled: Disable network
            
        Returns:
            ContainerResult
        """
        container = None
        try:
            container = await self.create_container(
        command=command,
        volumes=volumes,
        mem_limit=mem_limit,
        cpu_quota=cpu_quota,
        network_disabled=network_disabled,
        working_dir=working_dir,  # Make sure this is passed!
        read_only=read_only,
        cap_add=cap_add
    )
            
            result = await self.run_container(container, timeout=timeout)
            return result
            
        except Exception as e:
            logger.error(f"Container execution failed: {e}")
            return ContainerResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                execution_time_ms=0,
                error=str(e)
            )
        finally:
            if container:
                await self.cleanup_container(container)
    
    @asynccontextmanager
    async def temporary_container(self, **kwargs):
        """
        Context manager for temporary container execution.
        
        Automatically cleans up the container after use.
        
        Example:
            async with container_manager.temporary_container(
                command=["echo", "hello"]
            ) as container:
                result = await container_manager.run_container(container)
        """
        container = None
        try:
            container = await self.create_container(**kwargs)
            yield container
        finally:
            if container:
                await self.cleanup_container(container)
    
    async def build_image(
        self,
        dockerfile_path: str,
        tag: str,
        dockerfile: str = "Dockerfile",
        build_args: Optional[Dict[str, str]] = None
    ) -> bool:
        """
        Build a Docker image from a Dockerfile.
        
        Args:
            dockerfile_path: Path to directory containing Dockerfile
            tag: Image tag
            dockerfile: Name of the Dockerfile (relative to dockerfile_path)
            build_args: Build arguments
            
        Returns:
            True if build succeeded
        """
        try:
            loop = asyncio.get_event_loop()
            
            logger.info(f"Building Docker image '{tag}' from {dockerfile_path} using {dockerfile}")
            
            result = await loop.run_in_executor(
                None,
                lambda: self.client.images.build(
                    path=dockerfile_path,
                    dockerfile=dockerfile,
                    tag=tag,
                    buildargs=build_args or {},
                    rm=True,
                    forcerm=True
                )
            )
            
            image, build_logs = result
            
            for log in build_logs:
                if "stream" in log:
                    logger.debug(log["stream"].strip())
                elif "error" in log:
                    logger.error(log["error"].strip())
            
            logger.info(f"Successfully built image: {tag}")
            return True
            
        except DockerException as e:
            logger.error(f"Failed to build Docker image: {e}")
            return False
    
    def is_image_available(self, image: str) -> bool:
        """
        Check if a Docker image is available locally.
        
        Args:
            image: Image name and tag
            
        Returns:
            True if image exists
        """
        try:
            self.client.images.get(image)
            return True
        except NotFound:
            return False
        except Exception as e:
            logger.warning(f"Error checking image availability: {e}")
            return False


# Global container manager instance
_container_manager: Optional[ContainerManager] = None


def get_container_manager() -> ContainerManager:
    """Get or create the global container manager instance."""
    global _container_manager
    if _container_manager is None:
        _container_manager = ContainerManager()
    return _container_manager


async def create_container(*args, **kwargs):
    """Create a container using the global manager."""
    return await get_container_manager().create_container(*args, **kwargs)


async def run_container(*args, **kwargs):
    """Run a container using the global manager."""
    return await get_container_manager().run_container(*args, **kwargs)


async def cleanup_container(*args, **kwargs):
    """Clean up a container using the global manager."""
    return await get_container_manager().cleanup_container(*args, **kwargs)


async def execute_command(*args, **kwargs):
    """Execute a command in a container using the global manager."""
    return await get_container_manager().execute_command(*args, **kwargs)
