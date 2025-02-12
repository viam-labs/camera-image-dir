"""
This file registers the model with the Python SDK.
"""

from viam.components.camera import Camera
from viam.resource.registry import Registry, ResourceCreatorRegistration

from .imageDir import imageDir

Registry.register_resource_creator(Camera.API, imageDir.MODEL, ResourceCreatorRegistration(imageDir.new, imageDir.validate))
