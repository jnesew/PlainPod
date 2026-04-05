from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

# Allow running from source checkout without requiring PYTHONPATH=src.
__path__ = extend_path(__path__, __name__)
_src_pkg = Path(__file__).resolve().parent.parent / "src" / __name__
if _src_pkg.is_dir():
    __path__.append(str(_src_pkg))
