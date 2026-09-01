# config

See repository-level AGENTS.md for responsibilities and implementation constraints for this package.

This package is the city configuration/composition boundary: it loads city
profiles from `configs/cities/`, reads runtime provider settings from the
environment, and constructs concrete providers. It sits above `providers` and
must not be imported by `services`, `analytics`, `domain`, `storage`, `api`,
`mcp` or `agent`.
