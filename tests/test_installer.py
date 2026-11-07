"""Tests for the PDF Lite Windows Installer (MSI) package."""

import os
import unittest


class TestMsiPackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.msi_path = os.path.join(cls.repo_dir, "dist", "pdf-lite.msi")
        cls.wxs_path = os.path.join(cls.repo_dir, "installer", "Product.wxs")

    def test_msi_file_exists_and_sized(self):
        """Verify the MSI package exists and has expected size (> 30 MB)."""
        self.assertTrue(os.path.isfile(self.msi_path), f"MSI not found at {self.msi_path}")
        size = os.path.getsize(self.msi_path)
        self.assertGreater(size, 30_000_000, f"MSI file unexpectedly small: {size} bytes")

    def test_wxs_configuration(self):
        """Verify key configurations in Product.wxs."""
        self.assertTrue(os.path.isfile(self.wxs_path))
        with open(self.wxs_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('Name="PDF Lite"', content)
        self.assertIn('Manufacturer="Ken Huang"', content)
        self.assertIn('UpgradeCode="4C6F4B72-73A1-428E-B9D1-34F90D90A1A2"', content)
        self.assertIn('Id="PDFLite.Document"', content)
        self.assertIn('Extension Id="pdf"', content)
        self.assertIn('DesktopShortcut', content)
        self.assertIn('StartMenuShortcut', content)
        self.assertIn('WixUI_InstallDir', content)

    def test_msi_database_via_com(self):
        """Query the MSI database using WindowsInstaller.Installer COM object."""
        import subprocess
        import json
        ps_cmd = f"""
        $windowsInstaller = New-Object -ComObject WindowsInstaller.Installer
        $database = $windowsInstaller.OpenDatabase('{self.msi_path}', 0)
        $query = "SELECT Property, Value FROM Property"
        $view = $database.OpenView($query)
        $view.Execute()
        $props = @{{}}
        while ($rec = $view.Fetch()) {{
            $props[$rec.StringData(1)] = $rec.StringData(2)
        }}
        $props | ConvertTo-Json
        """
        result = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, f"PowerShell query failed: {result.stderr}")
        props = json.loads(result.stdout)
        self.assertEqual(props.get("ProductName"), "PDF Lite")
        self.assertEqual(props.get("Manufacturer"), "Ken Huang")
        self.assertEqual(props.get("ProductVersion"), "1.0.0")


if __name__ == "__main__":
    unittest.main()
