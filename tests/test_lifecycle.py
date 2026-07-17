from __future__ import annotations

import unittest

from pi_camera_studio.lifecycle import DeferredClose


class DeferredCloseTests(unittest.TestCase):
    def test_close_is_consumed_only_after_every_camera_job_finishes(self) -> None:
        close = DeferredClose()
        capture_job = object()
        detection_job = object()
        detection_frame = object()

        self.assertTrue(close.defer_if_busy(capture_job, detection_job, detection_frame))
        self.assertTrue(close.requested)
        self.assertFalse(close.consume_if_idle(None, detection_job, detection_frame))
        self.assertFalse(close.consume_if_idle(None, None, detection_frame))
        self.assertTrue(close.consume_if_idle(None, None, None))
        self.assertFalse(close.requested)
        self.assertFalse(close.consume_if_idle(None, None, None))

    def test_idle_close_is_not_deferred(self) -> None:
        close = DeferredClose()
        self.assertFalse(close.defer_if_busy(None, None))
        self.assertFalse(close.requested)


if __name__ == "__main__":
    unittest.main()
