import pathlib
from typing import Union, Dict

from sashimi.file.flatten import flatten_directory as _flatten_directory


def flatten(source_dir: Union[str, pathlib.Path]) -> Dict[str, int]:
    """
    Synchronously flatten a directory structure.
    Moves all files from subdirectories into the source directory root
    and deletes any resulting empty directories.

    Returns a dictionary of operation statistics.
    """
    resolved_path = pathlib.Path(source_dir).resolve()
    return _flatten_directory(resolved_path)
