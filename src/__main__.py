import asyncio
import sys

from viam.module.module import Module
from viam.components.camera import Camera
from .imageDir import imageDir

async def main():
    """This function creates and starts a new module, after adding all desired resources.
    Resources must be pre-registered. See the `__init__.py` file.
    """
    module = Module.from_args()
    module.add_model_from_registry(Camera.API, imageDir.MODEL)
    await module.start()

if __name__ == "__main__":
    asyncio.run(main())
