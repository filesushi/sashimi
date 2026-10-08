import pathlib
from typing import Union, Tuple, Optional

from sashimi.file.extract import extract_video_segment as _extract_video_segment


def extract(
    input_path: Union[str, pathlib.Path],
    start_str: str,
    end_str: str,
    output_path: Optional[Union[str, pathlib.Path]] = None,
    force: bool = False,
) -> Tuple[str, str, pathlib.Path]:
    """
    Extracts a specific portion of a video file without re-encoding.
    Returns: (status_string, diagnostic_message, target_output_path)
    """
    resolved_input = pathlib.Path(input_path).resolve()
    resolved_output = pathlib.Path(output_path).resolve() if output_path else None

    return _extract_video_segment(
        input_path=resolved_input, start_str=start_str, end_str=end_str, output_path=resolved_output, force=force
    )
