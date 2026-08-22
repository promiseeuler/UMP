# Versioning and Releases

UMP uses semantic versioning for the Python package. Before `1.0.0`, minor
versions may change unstable Python APIs. Wire compatibility follows the
separate protocol-version rules in `PROTOCOL.md`.

Release tags use `vMAJOR.MINOR.PATCH` and must match `pyproject.toml`. A release
build must run the complete test suite, build both wheel and source archives,
validate package metadata, install the wheel in an isolated environment, and
verify the public command-line surfaces.

Release commits and all ordinary development commits follow Conventional
Commits as defined in the repository `CONTRIBUTING.md`. An incompatible public
API or protocol change must use `!` or a `BREAKING CHANGE:` footer and include
protocol migration notes.

Release artifacts do not certify UMP or a manufacturer adapter for physical
robot operation.
