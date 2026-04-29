"""Composable pipeline steps.

Each step is a self-contained Python module exposing a pure compute function
(input DataFrame -> output DataFrame + stats) and an optional CLI wrapper for
standalone invocation. Step 5 will add a runner that composes named steps
into strategies via YAML.
"""
