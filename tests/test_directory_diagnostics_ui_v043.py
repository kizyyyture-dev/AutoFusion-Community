"""GUI test verifies intentional-only readonly diagnosis, without any third-party app."""
import os
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from pathlib import Path

from app import App
from directory_diagnostics import FolderHint

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','GUI display not available')
class DirectoryDiagnosticUITests(unittest.TestCase):
    def setUp(self):
        self.app=App(); self.app.update_idletasks()

    def tearDown(self):
        w=getattr(self.app,'_diag_window',None)
        if w is not None and w.winfo_exists(): w.destroy()
        self.app.destroy()

    def _tree(self,root):
        for child in root.winfo_children():
            if isinstance(child,ttk.Treeview): return child
            found=self._tree(child)
            if found: return found
        return None

    def test_dialog_shows_readonly_results_without_launching_programs(self):
        hint=FolderHint('After Effects','偏好设置',Path('/x/Adobe/After Effects'),
                        '官方目录',False,'重要用户文件',True)
        with patch('app.scan_known_locations',return_value=[hint]),patch('app.subprocess.Popen',side_effect=AssertionError('no external executables')):
            self.app._switch_page('settings')
            self.app._open_directory_diagnostics()
            diag=self.app._diag_window
            for _ in range(30):
                self.app.update()
                tree=self._tree(diag)
                if tree and tree.get_children(): break
                time.sleep(.02)
            self.assertIsNotNone(tree)
            self.assertEqual(len(tree.get_children()),1)
            self.assertIn('偏好设置',tree.item(tree.get_children()[0],'values'))
            self.assertIn('只读',diag.title())

    def test_reopen_focuses_single_diagnostic_window(self):
        with patch('app.scan_known_locations',return_value=[]):
            self.app._open_directory_diagnostics()
            one=self.app._diag_window
            self.app._open_directory_diagnostics()
            self.assertIs(one,self.app._diag_window)

if __name__=='__main__':unittest.main()
