import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Doit être défini avant l'import de l'application (config lue à l'import).
os.environ["MONTEUR_DATA_DIR"] = tempfile.mkdtemp(prefix="monteur-test-")
os.environ["MONTEUR_DISABLE_WHISPER"] = "1"
os.environ["MONTEUR_DISABLE_CLAUDE"] = "1"


@pytest.fixture(scope="session")
def rush(tmp_path_factory) -> Path:
    """Rush synthétique de 24 s : 'paroles' de 5 s séparées de silences, un pic sonore, 2 plans."""
    out = tmp_path_factory.mktemp("media") / "rush.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=12",
        "-f", "lavfi", "-i", "smptebars=s=640x360:r=30:d=12",
        "-f", "lavfi", "-i",
        "aevalsrc='if(lt(mod(t,8),5),0.3*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*3*t)),0.001*random(0))"
        "+if(between(t,17,17.6),0.7*sin(2*PI*600*t),0)':s=48000:d=24",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out),
    ], check=True)
    return out
