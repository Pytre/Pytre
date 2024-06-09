import sys
from pathlib import Path

"""add parent folder to sys path to allow scripts direct run"""
main_dir = str(Path(__file__).parents[1])
if main_dir not in sys.path:
    sys.path.insert(0, main_dir)
