"""Offline regression coverage: Mobile identity must describe the ADB target."""
import os
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from galaxy.client.components.device_registry import DeviceRegistry
from ufo.client.device_info_provider import DeviceInfoProvider
from ufo.client.websocket import UFOWebSocketClient


class MobileIdentityTests(unittest.TestCase):
    def test_mobile_uses_selected_adb_target_not_linux_host(self):
        with patch.dict(os.environ, {"ANDROID_SERIAL": "phone-serial"}, clear=True), \
                patch("ufo.client.device_info_provider.subprocess.run",
                      return_value=SimpleNamespace(stdout="15\n")) as adb, \
                patch("platform.system", side_effect=AssertionError("Must not inspect host OS")), \
                patch("socket.gethostname", side_effect=AssertionError("Must not inspect host hostname")):
            info = DeviceInfoProvider.collect_system_info("mobile_1", {"label": "phone"}, target_platform="mobile")
        adb.assert_called_once_with(
            ["adb", "-s", "phone-serial", "shell", "getprop", "ro.build.version.release"],
            capture_output=True, text=True, encoding="utf-8", timeout=5, check=True,
        )
        self.assertEqual(info.platform, "mobile")
        self.assertEqual(info.platform_type, "mobile")
        self.assertEqual(info.os_version, "Android 15")
        self.assertEqual(set(info.supported_features), {"mobile", "android", "mobile_touch", "mobile_apps"})
        self.assertEqual((info.cpu_count, info.memory_total_gb, info.hostname, info.ip_address),
                         (0, 0.0, "unknown", "unknown"))
        self.assertEqual(info.custom_metadata, {"label": "phone"})

    def test_adb_failure_retains_mobile_identity(self):
        errors = [FileNotFoundError("adb"), subprocess.TimeoutExpired(["adb"], 5),
                  subprocess.CalledProcessError(1, ["adb"], stderr="more than one device/emulator"),
                  subprocess.CalledProcessError(1, ["adb"], stderr="device unauthorized"),
                  UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")]
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    patch("ufo.client.device_info_provider.subprocess.run", side_effect=error), \
                    patch("platform.system", side_effect=AssertionError("No host fallback")):
                info = DeviceInfoProvider.collect_system_info("mobile_1", target_platform="mobile")
                self.assertEqual(info.platform, "mobile")
                self.assertEqual(info.os_version, "unknown")
                self.assertNotIn("linux_apps", info.supported_features)

    def test_empty_version_and_unspecified_serial_do_not_guess_device(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("ufo.client.device_info_provider.subprocess.run",
                      return_value=SimpleNamespace(stdout="\n")) as adb:
            info = DeviceInfoProvider.collect_system_info("mobile_1", target_platform="mobile")
        self.assertEqual(adb.call_args.args[0], ["adb", "shell", "getprop", "ro.build.version.release"])
        self.assertEqual(info.os_version, "unknown")
        self.assertEqual(info.platform_type, "mobile")

    def test_desktop_and_old_call_signature_keep_existing_behavior(self):
        for host, target in (("Linux", None), ("Linux", "linux"), ("Windows", "windows"), ("Darwin", None)):
            with self.subTest(host=host, target=target), \
                    patch("platform.system", return_value=host), \
                    patch.object(DeviceInfoProvider, "_get_os_version", return_value="host-version"), \
                    patch.object(DeviceInfoProvider, "_get_cpu_count", return_value=4), \
                    patch.object(DeviceInfoProvider, "_get_memory_total_gb", return_value=8.0), \
                    patch.object(DeviceInfoProvider, "_get_hostname", return_value="host"), \
                    patch.object(DeviceInfoProvider, "_get_ip_address", return_value="127.0.0.1"), \
                    patch("ufo.client.device_info_provider.subprocess.run") as adb:
                kwargs = {} if target is None else {"target_platform": target}
                info = DeviceInfoProvider.collect_system_info("desktop", {"tag": "existing"}, **kwargs)
                self.assertEqual(info.platform, host.lower())
                self.assertEqual(info.platform_type, "computer")
                self.assertEqual(info.os_version, "host-version")
                self.assertEqual(info.cpu_count, 4)
                self.assertIn("cli", info.supported_features)
                self.assertEqual(info.custom_metadata, {"tag": "existing"})
                adb.assert_not_called()


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_metadata_survives_galaxy_registry_update(self):
        client = UFOWebSocketClient("ws://unused", SimpleNamespace(client_id="mobile_1", platform="mobile"))
        client.registration_protocol = Mock(register_as_device=AsyncMock(return_value=True))
        main_thread = threading.get_ident()
        query_threads = []

        def read_version(*args, **kwargs):
            query_threads.append(threading.get_ident())
            return SimpleNamespace(stdout="14")

        with patch("ufo.client.device_info_provider.subprocess.run", side_effect=read_version), \
                patch("platform.system", return_value="Linux"):
            await client.register_client()
        wire = client.registration_protocol.register_as_device.call_args.kwargs
        self.assertEqual(wire["platform"], "mobile")
        self.assertEqual(wire["metadata"]["system_info"]["platform"], "mobile")
        self.assertTrue(client.connected_event.is_set())
        self.assertTrue(query_threads)
        self.assertNotEqual(query_threads[0], main_thread)

        registry = DeviceRegistry()
        profile = registry.register_device("mobile_1", "ws://unused", os="mobile")
        registry.update_device_system_info("mobile_1", wire["metadata"]["system_info"])
        self.assertEqual(profile.os, "mobile")
        self.assertEqual(set(profile.capabilities), {"mobile", "android", "mobile_touch", "mobile_apps"})
        self.assertEqual(profile.metadata["system_info"]["os_version"], "Android 14")


if __name__ == "__main__":
    unittest.main()
