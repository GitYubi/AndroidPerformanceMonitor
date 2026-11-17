"""Build the shipped Android DEX runner with JDK 17 and a pinned R8 JAR."""
from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent
R8_SHA256 = '900dfbc649519969fc5a4c7520d6b7355338e565fa1249874e0190b8d61b1199'

def build(r8: Path):
    with r8.open('rb') as contents:
        if hashlib.file_digest(contents, 'sha256').hexdigest() != R8_SHA256:
            raise ValueError('R8 checksum mismatch')
    with tempfile.TemporaryDirectory() as temp:
        classes = Path(temp)
        subprocess.run(['javac', '--release', '8', '-d', temp, str(SOURCE / 'Runner.java')], check=True)
        subprocess.run(['java', '-cp', str(r8.resolve()), 'com.android.tools.r8.D8',
                        '--min-api', '30', '--output', str(SOURCE.parent / 'multiscreen-runner.jar'),
                        *map(str, sorted(classes.rglob('*.class')))], check=True)

if __name__ == '__main__':
    build(Path(sys.argv[1]))
