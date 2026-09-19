"""Shutdown joins the config worker before runtime stores may be closed."""
import threading
from unittest.mock import patch

from test_pending_revisions import CloudDeliveryFixture
from tmbox_gateway.central_sync import CentralRuntimeManifest
from tmbox_gateway.local_server import _cloud_auto_sync_loop, _stop_cloud_sync


class CloudShutdownTests(CloudDeliveryFixture):
    def test_stop_during_download_joins_worker_without_applying_config(self):
        started, release, stopped = threading.Event(), threading.Event(), threading.Event()
        stop = threading.Event()
        fetch = self.application.linked_runtime_fetcher

        def blocked_fetch(token, url, manifest_only):
            result = fetch(token, url, manifest_only)
            if not manifest_only:
                started.set()
                self.assertTrue(release.wait(2))
            return result

        self.application.linked_runtime_fetcher = blocked_fetch
        worker = threading.Thread(target=_cloud_auto_sync_loop, args=(self.application, None, stop))
        with patch.object(self.application.cloud_config, "wait_for_change") as wait:
            worker.start()
            self.assertTrue(started.wait(2))

            def shutdown():
                _stop_cloud_sync(self.application, worker, stop)
                stopped.set()

            closer = threading.Thread(target=shutdown)
            closer.start()
            self.assertTrue(stop.wait(2))
            self.assertFalse(stopped.wait(.03))
            # Stores are still open while the worker owns in-flight work.
            self.assertEqual("cloud-first", self.runtime.active().publication_id)
            release.set()
            closer.join(2)
            self.assertFalse(closer.is_alive())
            self.assertFalse(worker.is_alive())
            wait.assert_not_called()
        self.assertEqual("cloud-first", self.runtime.active().publication_id)
        self.assertIsNone(self.application.lifecycle.transition())
        self.assertIsNone(self.runtime.pending_publication())

    def test_stop_during_long_poll_waits_then_exits_without_new_check(self):
        started, release = threading.Event(), threading.Event()
        stop = threading.Event()
        self.offered["publication_id"] = "cloud-first"
        self.application.cloud_config.notifications_supported = True
        fetched = self.application.linked_runtime_fetcher

        def fetch(token, url, manifest_only):
            result = fetched(token, url, manifest_only)
            if manifest_only:
                return CentralRuntimeManifest(result.publication_id, result.published_at, result.package_checksum, True)
            return result

        def wait(*_args):
            started.set()
            self.assertTrue(release.wait(2))
            return CentralRuntimeManifest("cloud-first", "", "", True)

        self.application.linked_runtime_fetcher = fetch
        worker = threading.Thread(target=_cloud_auto_sync_loop, args=(self.application, None, stop))
        with patch("tmbox_gateway.cloud_config.wait_for_runtime_change", side_effect=wait) as waiting:
            worker.start()
            self.assertTrue(started.wait(2))
            timer = threading.Timer(.03, release.set)
            timer.start()
            _stop_cloud_sync(self.application, worker, stop)
            timer.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(1, waiting.call_count)
        self.assertEqual([True], self.fetches)
        self.assertFalse(self.application.cloud_config.wait_for_change())
        self.assertFalse(self.application.auto_sync_cloud_runtime()["checked"])
