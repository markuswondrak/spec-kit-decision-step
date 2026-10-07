# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-10-07

### Added

- `decision` workflow step (`DecisionStep`) with typed `choice`, `score`, and
  `noul` question types and native probabilities.
- Backend adapters: hosted Jev over HTTP, Laya over HTTP or in-process, and a
  custom-backend plugin interface.
- Layered backend configuration from package defaults through project, local,
  and step-level settings.
- Structured context assembly from workflow expressions and project files, with
  path-safety checks and truncation warnings.
- Host-free unit tests plus an opt-in `--integration` suite that exercises the
  step against Spec Kit.

[Unreleased]: https://github.com/markuswondrak/spec-kit-decision-step/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/markuswondrak/spec-kit-decision-step/releases/tag/v0.9.0
