"""Heartbeat wire identities must match the role used at registration."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from aip.messages import ClientMessage, ClientMessageType, ClientType
from aip.protocol.heartbeat import HeartbeatProtocol
from galaxy.client.components.heartbeat_manager import HeartbeatManager


class HeartbeatIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_device_heartbeat_preserves_default_role(self):
        transport = Mock(send=AsyncMock())
        await HeartbeatProtocol(transport).send_heartbeat("device_1")
        message = ClientMessage.model_validate_json(transport.send.call_args.args[0])
        self.assertEqual(message.client_type, ClientType.DEVICE)
        self.assertEqual(message.client_id, "device_1")

    async def test_galaxy_heartbeat_uses_registered_constellation_identity(self):
        transport = Mock(send=AsyncMock())
        connection = Mock(task_name="session_1")
        connection._transports = {"device_1": transport}
        connection.is_connected.side_effect = [True, False]
        manager = HeartbeatManager(connection, Mock())
        with patch(
            "galaxy.client.components.heartbeat_manager.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await manager._heartbeat_loop("device_1")
        transport.send.assert_awaited_once()
        message = ClientMessage.model_validate_json(transport.send.call_args.args[0])
        self.assertEqual(message.type, ClientMessageType.HEARTBEAT)
        self.assertEqual(message.client_type, ClientType.CONSTELLATION)
        self.assertEqual(message.client_id, "session_1@device_1")
        self.assertEqual(message.metadata, {"device_id": "device_1"})
