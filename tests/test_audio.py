from __future__ import annotations

import unittest

from pi_camera_studio.audio import parse_arecord_devices


ARECORD_SAMPLE = """\
**** List of CAPTURE Hardware Devices ****
card 2: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 3: Array [Microphone Array], device 1: Audio Capture [Audio Capture]
"""


class AudioTests(unittest.TestCase):
    def test_parse_arecord_capture_devices(self) -> None:
        sources = parse_arecord_devices(ARECORD_SAMPLE)
        self.assertEqual([source.device for source in sources], ["hw:2,0", "hw:3,1"])
        self.assertTrue(all(source.input_format == "alsa" for source in sources))
        self.assertIn("USB PnP Sound Device", sources[0].label)

    def test_parse_empty_inventory(self) -> None:
        self.assertEqual(parse_arecord_devices("no soundcards found"), [])


if __name__ == "__main__":
    unittest.main()
