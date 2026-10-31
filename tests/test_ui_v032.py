"""Smooth buttons and primary-action style regressions."""
import base64
import os
import struct
import tkinter as tk
import unittest
from app import _pill_image_png, ACTION, DARK, Pill

class PngTests(unittest.TestCase):
    def test_valid_antialiased_rgba_png(self):
        raw=base64.b64decode(_pill_image_png(128,43,ACTION,ACTION))
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack('>II',raw[16:24]),(128,43))
        self.assertEqual(raw[25],6)  # RGBA

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','No GUI')
class VisualButtonTests(unittest.TestCase):
    def test_accent_button_and_dark_active_nav(self):
        root=tk.Tk()
        try:
            accent=Pill(root,'启动',lambda:None,primary=True)
            nav=Pill(root,'操作台',lambda:None,active=True)
            accent.pack(); nav.pack(); root.update()
            self.assertNotEqual(ACTION,DARK)
            self.assertGreaterEqual(len(root._pill_photo_cache),2)
        finally:
            root.destroy()

if __name__=='__main__': unittest.main()
