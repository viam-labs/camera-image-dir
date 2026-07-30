// Package main is the module entrypoint for the image-dir camera.
package main

import (
	"go.viam.com/rdk/components/camera"
	"go.viam.com/rdk/module"
	"go.viam.com/rdk/resource"

	"github.com/viam-labs/camera-image-dir/models/imagedir"
)

func main() {
	module.ModularMain(resource.APIModel{API: camera.API, Model: imagedir.Model})
}
