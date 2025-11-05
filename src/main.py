import asyncio

from viam.module.module import Module
from viam.components.camera import Camera 

try:
    from src.models.image_dir import imageDir
except ModuleNotFoundError:
    # when running as local module with run.sh
    from .models.image_dir import imageDir

if __name__ == '__main__':
    asyncio.run(Module.run_from_registry())