# Repository Guidelines

## Project Structure & Module Organization

This repository is currently a starter directory: it has no application code, tests, assets, or build configuration. As the project grows, keep application code in `src/`, automated tests in `tests/`, and static files in `assets/`. Group related code by feature when that makes ownership and navigation clearer. Update this guide when the actual structure is established.

## Build, Test, and Development Commands

No package manager, build tool, or local run command is configured yet. When adding a toolchain, document the exact setup, development, build, and test commands here and in the project README. Prefer commands that work from the repository root, and identify any required environment variables or external services.

## Coding Style & Naming Conventions

Follow the conventions of the language and framework selected for the project. Keep indentation consistent within each file, use descriptive names, and choose one naming pattern for similar files and modules. Add formatter and linter configuration with the first source code, then document how contributors run those checks. Avoid introducing a style rule that tooling cannot enforce consistently.

## Testing Guidelines

No test framework or coverage threshold is configured. Add focused tests alongside new behavior, covering expected results and meaningful failure cases. Name test files so they are easy to find from the code they exercise, such as `tests/followers.test.js` for `src/followers.js`. Document the test command after selecting a framework.

## Commits & Pull Requests

There is no usable Git history from which to infer an existing commit convention. Until one is established, use short, imperative subjects such as `Add follower comparison`. Pull requests should describe the change, explain how it was checked, link a relevant issue when one exists, and include screenshots for visible interface changes.
