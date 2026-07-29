"""One-time fix for Microsoft Office's cloud-first Save default.

Confirmed live (2026-07-29): Word (and, by the same shared setting, Excel/
PowerPoint) opens a custom "Save As backstage" screen defaulting to OneDrive
whenever a document is saved for the first time, instead of the plain Windows
common Save dialog our save_file tool (system_tools.py) already knows how to
drive deterministically. This isn't an app quirk to route around every save —
it's a documented, permanent Office setting: HKCU\\...\\Office\\<ver>\\Common\\
General\\PreferCloudSaveLocations, 1 (or absent/default) prefers cloud
locations, 0 makes Save/Save As go straight to a local-folder Explorer-style
dialog instead. Setting it once, idempotently, at startup removes the need to
navigate Office's cloud-first screen at all — the existing save_file tool's
common-dialog handling then just works.
"""
import logging
import winreg

logger = logging.getLogger(__name__)

# Office version registry hives share this same setting; try the versions
# actually in current use (16.0 covers 2016 through current 365 builds,
# by far the common case) plus older ones as a harmless no-op if absent.
_OFFICE_VERSIONS = ("16.0", "15.0", "14.0")


def disable_office_cloud_default_save():
    """Best-effort, non-fatal: sets PreferCloudSaveLocations=0 for every
    installed Office version hive found under HKCU. Safe to call on every
    startup — skips versions that don't exist, and is a no-op if already set."""
    for version in _OFFICE_VERSIONS:
        key_path = rf"Software\Microsoft\Office\{version}\Common\General"
        try:
            # Only touch versions that actually exist for this install — a
            # version with no Common\General subkey isn't installed, and
            # creating one there would just be inert clutter, not a real fix.
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Office\{version}"):
                pass
        except FileNotFoundError:
            continue
        try:
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
            try:
                current, _ = winreg.QueryValueEx(key, "PreferCloudSaveLocations")
            except FileNotFoundError:
                current = None
            if current != 0:
                winreg.SetValueEx(key, "PreferCloudSaveLocations", 0, winreg.REG_DWORD, 0)
                logger.info(f"Office {version}: set PreferCloudSaveLocations=0 (was {current}) — Save now defaults to a local folder, not OneDrive.")
            winreg.CloseKey(key)
        except Exception:
            logger.exception(f"Office {version}: failed to set PreferCloudSaveLocations (non-fatal, save_file's custom-dialog fallback still applies).")
