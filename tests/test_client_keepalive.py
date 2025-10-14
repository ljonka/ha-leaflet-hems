"""Test the keepalive functionality of NymeaClient."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.leaflet_hems.client import NymeaClient


@pytest.mark.asyncio
async def test_keepalive_start_stop():
    """Test starting and stopping keepalive."""
    client = NymeaClient()
    
    # Mock the connection
    client._writer = AsyncMock()
    client._reader = AsyncMock()
    
    # Mock send_request
    with patch.object(client, 'send_request', new_callable=AsyncMock) as mock_send:
        # Start keepalive with short interval for testing
        await client.start_keepalive(interval=0.01)
        
        # Wait for a few iterations
        await asyncio.sleep(0.05)
        
        # Stop keepalive
        await client.stop_keepalive()
        
        # Check that send_request was called multiple times
        assert mock_send.call_count >= 2
        
        # Verify the keepalive payload structure
        for call in mock_send.call_args_list:
            payload = call[0][0]  # First argument
            assert payload["method"] == "JSONRPC.KeepAlive"
            assert "id" in payload
            assert payload["params"] == {}


@pytest.mark.asyncio
async def test_keepalive_handles_exceptions():
    """Test that keepalive handles send exceptions gracefully."""
    client = NymeaClient()
    
    # Mock the connection
    client._writer = AsyncMock()
    client._reader = AsyncMock()
    
    # Mock send_request to raise an exception
    with patch.object(client, 'send_request', new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = Exception("Connection error")
        
        # Start keepalive
        await client.start_keepalive(interval=0.01)
        
        # Wait briefly
        await asyncio.sleep(0.03)
        
        # Stop keepalive
        await client.stop_keepalive()
        
        # Should have attempted to send despite exceptions
        assert mock_send.call_count >= 1


@pytest.mark.asyncio
async def test_keepalive_not_started_twice():
    """Test that keepalive doesn't start twice."""
    client = NymeaClient()
    
    # Mock the connection
    client._writer = AsyncMock()
    client._reader = AsyncMock()
    
    with patch.object(client, 'send_request', new_callable=AsyncMock):
        # Start keepalive
        await client.start_keepalive(interval=0.01)
        
        # Try to start again
        await client.start_keepalive(interval=0.01)
        
        # Should only have one task
        assert client._keepalive_task is not None
        
        # Stop keepalive
        await client.stop_keepalive()
        
        # Task should be None after stop
        assert client._keepalive_task is None


@pytest.mark.asyncio
async def test_close_stops_keepalive():
    """Test that close() stops the keepalive task."""
    client = NymeaClient()
    
    # Mock the connection
    client._writer = AsyncMock()
    client._reader = AsyncMock()
    
    with patch.object(client, 'send_request', new_callable=AsyncMock):
        # Start keepalive
        await client.start_keepalive(interval=0.01)
        
        # Verify task is running
        assert client._keepalive_task is not None
        
        # Close client
        await client.close()
        
        # Task should be stopped
        assert client._keepalive_task is None
