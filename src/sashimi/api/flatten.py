# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

import logging
import pathlib
import os

__all__ = ["flatten"]

logger = logging.getLogger("sashimi")


def flatten(source_dir: str | pathlib.Path) -> dict[str, int]:
    """
    Flattens a directory by moving all files from its subdirectories
    into the root of the source directory, using pathlib.

    Handles filename conflicts by prepending normalized parent directory paths.
    Removes empty subdirectories afterwards.

    Returns a dictionary with operation statistics.
    """
    source_dir_path = pathlib.Path(source_dir)
    if not source_dir_path.exists():
        msg = f"Error: The specified source directory does not exist: {source_dir_path}"
        logger.error(msg)
        raise FileNotFoundError(msg)
    if not source_dir_path.is_dir():
        msg = f"Error: The specified path is not a directory: {source_dir_path}"
        logger.error(msg)
        raise NotADirectoryError(msg)

    logger.info(f"Starting to flatten directory: {source_dir_path}")

    moved_files_count = 0
    renamed_files_count = 0

    # 1. Walk through the directory tree to move files
    for item_path in source_dir_path.rglob("*"):
        # Only process files that are *not* directly in the source directory
        if item_path.is_file() and item_path.parent != source_dir_path:
            original_file_path = item_path
            filename = item_path.name
            destination_file_path = source_dir_path / filename

            # 2. Handle potential filename clashes
            if destination_file_path.exists():
                logger.warning(f"Filename clash detected for '{filename}'. Renaming to ensure uniqueness.")
                renamed_files_count += 1

                relative_dir = original_file_path.parent.relative_to(source_dir_path)
                normalized_prefix = str(relative_dir).replace(os.sep, "_")

                base = filename[: filename.rfind(item_path.suffix)] if item_path.suffix else filename
                ext = item_path.suffix

                new_filename = f"{normalized_prefix}_{base}{ext}"
                destination_file_path = source_dir_path / new_filename

                counter = 1
                while destination_file_path.exists():
                    new_filename = f"{normalized_prefix}_{base}_{counter}{ext}"
                    destination_file_path = source_dir_path / new_filename
                    counter += 1

            # 3. Move the file to the source directory
            try:
                original_file_path.rename(destination_file_path)
                moved_files_count += 1
                logger.info(
                    f"Moved '{original_file_path.relative_to(source_dir_path)}' -> '{destination_file_path.name}'"
                )
            except Exception as e:
                logger.error(f"Could not move file {original_file_path}. Reason: {e}")

    # 4. Remove empty subdirectories (bottom-up traversal)
    removed_dirs_count = 0
    for dirpath_str, _dirnames, _filenames in os.walk(str(source_dir_path), topdown=False):
        dirpath = pathlib.Path(dirpath_str)
        if dirpath == source_dir_path:
            continue

        try:
            if not os.listdir(dirpath_str):
                try:
                    dirpath.rmdir()
                    removed_dirs_count += 1
                    logger.info(f"Removed empty directory: {dirpath.relative_to(source_dir_path)}")
                except OSError as e:
                    logger.error(f"Error removing directory {dirpath}. Reason: {e}")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"Error checking directory {dirpath}. Reason: {e}")

    logger.info("Directory flattening complete.")

    return {"moved": moved_files_count, "renamed": renamed_files_count, "removed_dirs": removed_dirs_count}
