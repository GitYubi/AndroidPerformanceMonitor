"""Stage record directories while their indexes are removed."""
from pathlib import Path
import shutil
import uuid

DELETE_STAGE_PREFIX = ".delete-"


def stage_directory(path: Path):
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_dir():
        raise ValueError('记录目录无效，无法删除')
    temporary = path.with_name(f'{DELETE_STAGE_PREFIX}{uuid.uuid4().hex}')
    path.replace(temporary)
    return path, temporary


def restore_directory(staged):
    if staged and staged[1].exists():
        staged[1].replace(staged[0])


def finish_directory(staged):
    if not staged:
        return []
    try:
        shutil.rmtree(staged[1])
        return []
    except OSError:
        return [str(staged[1])]
