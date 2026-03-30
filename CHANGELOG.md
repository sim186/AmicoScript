# Changelog

All notable changes to this project will be documented in this file.
This project adheres to Semantic Versioning (https://semver.org/) and the
Keep a Changelog format.

## [Unreleased]

### Added
- AAC (`.aac`) audio file support.

## [1.0.0] - 2026-03-30

- Fixed PyInstaller packaging for speaker diarization by bundling `pyannote.audio` data files (including `telemetry/config.yaml`) in standalone builds.
- Fixed windowed (`--noconsole`) runtime crash during diarization (`'NoneType' object has no attribute 'write'`) by providing safe stdio fallbacks for libraries that write to `stdout`/`stderr`.
- Fixed GitHub Actions release workflow: corrected `artifacts` parameter and added `allowUpdates` to support multi-OS parallel builds.
- Initial stable release.
