Both docker files must access the source code of this repository. Therefore the build context must be the root of the project directory.
Take a look into the build script for the normal py-kms version, as it demonstrates exactly that case and how to use these docker files.

## WebUI Authentication
If you run with `WEBUI=1`, you can protect the web interface with environment variables:

- `PYKMS_WEBUI_PASSWORD` (required to enable auth)
- `PYKMS_WEBUI_USERNAME` (optional, defaults to `admin`)
- `PYKMS_WEBUI_SECRET_KEY` (optional but recommended for stable Flask sessions)
