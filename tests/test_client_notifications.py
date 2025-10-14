"""Test the notification functionality of NymeaClient."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.leaflet_hems.client import NymeaClient


@pytest.mark.asyncio
async def test_notification_callback_registration():
    """Test registering and unregistering notification callbacks."""
    client = NymeaClient()
    
    # Mock callback
    callback = Mock()
    
    # Register callback
    token = client.register_notification_callback(callback)
    assert token is not None
    assert len(client._notification_callbacks) == 1
    
    # Unregister callback
    client.unregister_notification_callback(token)
    assert len(client._notification_callbacks) == 0


@pytest.mark.asyncio
async def test_notification_dispatch():
    """Test that notifications are properly dispatched to callbacks."""
    client = NymeaClient()
    
    # Mock callbacks
    callback1 = Mock()
    callback2 = Mock()
    
    # Register callbacks
    token1 = client.register_notification_callback(callback1)
    token2 = client.register_notification_callback(callback2)
    
    # Create test notification
    notification = {
        "method": "Energy.PowerBalanceChanged",
        "params": {
            "currentPowerConsumption": 1500,
            "currentPowerProduction": 2000
        }
    }
    
    # Dispatch notification
    await client._dispatch_notification(notification)
    
    # Verify both callbacks were called
    callback1.assert_called_once_with(notification)
    callback2.assert_called_once_with(notification)
    
    # Clean up
    client.unregister_notification_callback(token1)
    client.unregister_notification_callback(token2)


@pytest.mark.asyncio
async def test_notification_callback_exception_handling():
    """Test that exceptions in callbacks don't crash the dispatcher."""
    client = NymeaClient()
    
    # Mock callbacks - one that raises exception, one that works
    callback1 = Mock(side_effect=Exception("Callback error"))
    callback2 = Mock()
    
    # Register callbacks
    token1 = client.register_notification_callback(callback1)
    token2 = client.register_notification_callback(callback2)
    
    # Create test notification
    notification = {"method": "Energy.PowerBalanceChanged", "params": {}}
    
    # Dispatch notification - should not raise exception
    await client._dispatch_notification(notification)
    
    # Both callbacks should have been called despite exception in first
    callback1.assert_called_once_with(notification)
    callback2.assert_called_once_with(notification)
    
    # Clean up
    client.unregister_notification_callback(token1)
    client.unregister_notification_callback(token2)


@pytest.mark.asyncio
async def test_reader_loop_notification_detection():
    """Test that the reader loop correctly identifies notifications."""
    client = NymeaClient()
    
    # Mock the reader to return test messages
    client._reader = AsyncMock()
    
    # Test messages
    notification_msg = json.dumps({
        "method": "Energy.PowerBalanceChanged",
        "params": {"currentPowerConsumption": 1500}
    }) + "\n"
    
    response_msg = json.dumps({
        "id": 123,
        "status": "success",
        "params": {"result": "ok"}
    }) + "\n"
    
    # Mock readline to return messages then EOF
    client._reader.readline.side_effect = [
        notification_msg.encode(),
        response_msg.encode(),
        b""  # EOF
    ]
    client._reader.at_eof.side_effect = [False, False, True]
    
    # Mock dispatch method
    with patch.object(client, '_dispatch_notification', new_callable=AsyncMock) as mock_dispatch:
        # Start reader loop
        await client._reader_loop()
        
        # Verify notification was dispatched
        mock_dispatch.assert_called_once()
        notification = mock_dispatch.call_args[0][0]
        assert notification["method"] == "Energy.PowerBalanceChanged"
        assert notification["params"]["currentPowerConsumption"] == 1500
        
        # Verify response was queued
        response = await client._response_queue.get()
        assert response["id"] == 123
        assert response["status"] == "success"


@pytest.mark.asyncio
async def test_reader_loop_start_stop():
    """Test starting and stopping the reader loop."""
    client = NymeaClient()
    
    # Mock connection
    client._reader = AsyncMock()
    client._reader.at_eof.return_value = False
    client._reader.readline.return_value = b""  # Empty line to stop loop
    
    # Start reader loop
    await client.start_reader_loop()
    assert client._reader_task is not None
    
    # Stop reader loop
    await client.stop_reader_loop()
    assert client._reader_task is None


@pytest.mark.asyncio
async def test_send_request_with_response():
    """Test sending request and receiving response via queue."""
    client = NymeaClient()
    
    # Mock connection
    client._writer = AsyncMock()
    client._reader = AsyncMock()
    
    # Mock send_request
    with patch.object(client, 'send_request', new_callable=AsyncMock) as mock_send:
        # Create expected response
        expected_response = {
            "id": 1000,  # Should match client._next_id
            "status": "success",
            "params": {"powerBalance": 1500}
        }
        
        # Put response in queue (simulating reader loop)
        await client._response_queue.put(expected_response)
        
        # Send request
        response = await client.send_request_with_response("Energy.GetPowerBalance")
        
        # Verify request was sent
        mock_send.assert_called_once()
        sent_payload = mock_send.call_args[0][0]
        assert sent_payload["method"] == "Energy.GetPowerBalance"
        assert sent_payload["id"] == 1000
        
        # Verify response was received
        assert response == expected_response


@pytest.mark.asyncio
async def test_send_request_with_response_timeout():
    """Test timeout handling in send_request_with_response."""
    client = NymeaClient()
    
    # Mock connection
    client._writer = AsyncMock()
    client._reader = AsyncMock()
    
    with patch.object(client, 'send_request', new_callable=AsyncMock):
        # Don't put any response in queue to trigger timeout
        
        # Should timeout
        with pytest.raises(asyncio.TimeoutError):
            await client.send_request_with_response("Energy.GetPowerBalance", timeout=0.1)
