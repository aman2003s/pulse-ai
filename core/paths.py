import os

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def models_dir():
    """Root folder for all local model/asset files.

    PULSE_MODELS_DIR overrides the default when set — a packaged/frozen build's
    __file__-relative path doesn't reliably land next to a sibling "models"
    folder (e.g. PyInstaller's onedir layout nests package files under an
    internal subfolder), so the installer's launcher sets this explicitly.
    Running from source (no env var set) keeps today's behavior unchanged.
    """
    return os.environ.get("PULSE_MODELS_DIR") or os.path.join(_PROJECT_ROOT, 'models')
