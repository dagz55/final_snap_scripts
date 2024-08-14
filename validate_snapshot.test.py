import unittest
from validate_snapshot import CustomSpinner

class TestCustomSpinner(unittest.TestCase):
    def test_init_with_default_style(self):
        spinner = CustomSpinner("test_spinner")
        self.assertEqual(spinner.style, "aesthetic")

if __name__ == '__main__':
    unittest.main()