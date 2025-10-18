from __future__ import annotations

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.models.mods import generate_ranked_mod_settings, init_mods

if __name__ == "__main__":
    init_mods()
    generate_ranked_mod_settings(enable_all="--all" in sys.argv)
