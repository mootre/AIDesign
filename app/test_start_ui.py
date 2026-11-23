import io
import json
import unittest
from unittest.mock import patch

import start_ui


class LauncherTests(unittest.TestCase):
    def test_current_server_opens_without_starting_duplicate(self):
        response=io.BytesIO(json.dumps({'build':start_ui.EXPECTED_BUILD}).encode())
        with patch('start_ui.urllib.request.urlopen',return_value=response), patch('start_ui.webbrowser.open') as opened, patch('start_ui.runpy.run_path') as run:
            self.assertEqual(start_ui.main(),0);opened.assert_called_once_with(start_ui.URL + "/training");run.assert_not_called()

    def test_old_server_has_clear_error_instead_of_silent_failure(self):
        response=io.BytesIO(b'{"ready":true}')
        with patch('start_ui.urllib.request.urlopen',return_value=response), patch('start_ui.webbrowser.open'), patch('start_ui.sys.platform','linux'), patch('start_ui.runpy.run_path') as run:
            self.assertEqual(start_ui.main(),2);run.assert_not_called()

    def test_no_server_starts_the_application(self):
        with patch('start_ui.urllib.request.urlopen',side_effect=OSError('offline')), patch('start_ui.runpy.run_path') as run:
            self.assertEqual(start_ui.main(),0);run.assert_called_once()


if __name__=='__main__':unittest.main()
