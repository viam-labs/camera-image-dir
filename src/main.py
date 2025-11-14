import asyncio

from viam.module.module import Module

try:
    from src.models.image_dir import imageDir
except ModuleNotFoundError:
    # when running as local module with run.sh
    from .models.image_dir import imageDir  # noqa: F401

if __name__ == "__main__":
    asyncio.run(Module.run_from_registry())
