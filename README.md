# image-dir camera modular service

*image-dir* is a Viam modular service that provides camera capabilities, based on sequential images captured in directories.

The model this module makes available is *viam-labs:camera:image-dir*

## Prerequisites

One or more readable directories containing images in jpg|png|gif format.
Within a directory, it is expected that images be specifically named in the format *integer*.*ext* starting with 0.jpg (or other accepted extension), increasing numerically.

For example:

```bash
0.jpg
1.jpg
2.jpg
3.jpg
4.jpg
```

## API

The image-dir resource implements the [rdk camera API](https://github.com/rdk/camera-api), specifically get_image().

On each get_image() call, the next image will be returned sequentially (based on integer filename).
If it is the first get_image() call for that directory since the component was initialized, the first image returned will be the one with the oldest timestamp - after which point images will be returned sequentially by [index](#index-integer).
After the last image is returned, the next get_image() call will return the image at the 0 index (start at the beginning sequentially).

The following can be passed via the *get_image()* extra parameter:

### dir (string, *required*)

The directory from which to read images, within [root_dir](#root_dir).

### ext (string)

The file extension to use when attempting to read the next image.
If not specified, will default to 'jpg'.
Accepted values are jpg|jpeg|png|gif.

### index (integer)

If specified, return the image with this index (if it exists).
Index is a proxy for the base filename - for example if [index](#index-integer) is *10* and [ext](#ext-string) is *jpg*, *10.jpg* will be returned if it exists.
Passing [index](#index-integer) will also reset the incremental index for [dir](#dir-string-required).

Example:

```python
camera.get_image(extra={"dir":"pix","index":0}) # returns /tmp/pix/0.jpg
camera.get_image(extra={"dir":"pix"}) # returns /tmp/pix/1.jpg
camera.get_image(extra={"dir":"pix"}) # returns /tmp/pix/2.jpg
camera.get_image(extra={"dir":"pix","index":1}) # returns /tmp/pix/1.jpg
camera.get_image(extra={"dir":"pix"}) # returns /tmp/pix/2.jpg
```

If *-1* passed as [index](#index-integer), it will reset at the beginning, which is in fact the image with the oldest timestamp in the [dir](#dir-string-required) - not always the 0 [index](#index-integer).

## Viam Service Configuration

Example attribute configuration:

```json
{
    "root_dir": "/tmp"
}
```

### root_dir (string)

If specified, specified [dir](#dir-string-required) on get_image() calls must exist within this location.
If not specified, will default to */tmp*

## Troubleshooting
