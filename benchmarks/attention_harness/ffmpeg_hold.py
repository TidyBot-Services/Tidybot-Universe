"""How long the last frame of a variable-rate video lasts, on every supported ffmpeg."""
import subprocess


def final_hold(seconds):
    """ffmpeg output options that make the last packet last this long.

    ffmpeg 6.1 (Ubuntu 24.04) refuses -r next to -vsync vfr. ffmpeg 4.4 (Ubuntu 22.04)
    has no setts duration. setts keeps every frame only without B frames.
    """
    options=subprocess.run(['ffmpeg','-hide_banner','-h','bsf=setts'],capture_output=True,timeout=10).stdout
    if b'-duration' in options:
        return ['-bf','0','-bsf:v',f'setts=duration={seconds:.9f}/TB']
    return ['-r',f'{1/seconds:.9f}']
