"""MCP Manager: manages MCP server connections and tools."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.tools.registry import ToolRegistry


class MCPManager:
    """
    Manages MCP (Model Context Protocol) server connections.
    
    Responsibilities:
    - Connect to configured MCP servers
    - Register MCP tools to the tool registry
    - Manage connection lifecycle
    - Handle connection errors and retries
    """
    
    def __init__(
        self,
        servers: dict[str, Any] | None = None,
        tools: ToolRegistry | None = None,
        connection_timeout: float = 10.0,
    ):
        """
        Initialize MCP Manager.
        
        Args:
            servers: MCP server configurations
            tools: Tool registry to register MCP tools
            connection_timeout: Timeout for connecting to MCP servers
        """
        self._servers = servers or {}
        self._tools = tools
        self._connection_timeout = connection_timeout
        
        self._stack: AsyncExitStack | None = None
        self._connected = False
        self._connecting = False
    
    @property
    def is_connected(self) -> bool:
        """Check if MCP servers are connected."""
        return self._connected
    
    @property
    def is_connecting(self) -> bool:
        """Check if currently connecting to MCP servers."""
        return self._connecting
    
    @property
    def has_servers(self) -> bool:
        """Check if any MCP servers are configured."""
        return bool(self._servers)
    
    def set_tools(self, tools: ToolRegistry) -> None:
        """Set the tool registry."""
        self._tools = tools
    
    async def connect(self) -> bool:
        """
        Connect to configured MCP servers.
        
        Returns:
            True if connection was successful, False otherwise
        """
        if self._connected or self._connecting or not self._servers:
            return self._connected
        
        if not self._tools:
            logger.warning("MCPManager: no tool registry set, cannot connect")
            return False
        
        self._connecting = True
        
        try:
            self._stack = AsyncExitStack()
            await self._stack.__aenter__()
            
            await asyncio.wait_for(
                self._connect_servers(),
                timeout=self._connection_timeout
            )
            
            self._connected = True
            self._hide_internal_tools()
            logger.info("MCP servers connected successfully")
            return True
            
        except asyncio.TimeoutError:
            logger.error("MCP servers connection timeout ({}s), continuing without MCP tools", 
                        self._connection_timeout)
            await self._cleanup()
            return False
            
        except Exception as e:
            logger.error("Failed to connect MCP servers: {}", e)
            await self._cleanup()
            return False
            
        finally:
            self._connecting = False
    
    async def _connect_servers(self) -> None:
        """Connect to all configured MCP servers."""
        from nanobot.agent.tools.mcp import connect_mcp_servers
        await connect_mcp_servers(self._servers, self._tools, self._stack)
    
    def _hide_internal_tools(self) -> None:
        """Hide workflow-internal tools from LLM visibility."""
        if not self._tools:
            return
        
        self._tools.hide_pattern_from_llm("mcp_workflow-engine_")
        
        for tool_name in (
            "work_form_open_form",
            "work_form_update_node_status",
            "work_form_update_node_fields",
            "work_form_close_form",
        ):
            self._tools.hide_from_llm(tool_name)
        
        logger.info("Workflow-internal tools hidden from LLM (still callable internally)")
    
    async def _cleanup(self) -> None:
        """Clean up MCP connection resources."""
        if self._stack:
            try:
                await self._stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            self._stack = None
    
    async def close(self) -> None:
        """Close all MCP server connections."""
        await self._cleanup()
        self._connected = False
        self._connecting = False
        logger.info("MCP connections closed")
    
    async def ensure_connected(self, timeout: float = 15.0) -> bool:
        """
        Ensure MCP servers are connected, with optional timeout.
        
        Args:
            timeout: Maximum time to wait for connection
            
        Returns:
            True if connected, False otherwise
        """
        if self._connected:
            return True
        
        try:
            await asyncio.wait_for(self.connect(), timeout=timeout)
            return self._connected
        except asyncio.TimeoutError:
            logger.error("MCP connection timeout ({}s), continuing without MCP", timeout)
            return False
        except Exception as e:
            logger.error("MCP connection error: {}", e)
            return False
