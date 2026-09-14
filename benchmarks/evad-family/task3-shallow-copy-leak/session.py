"""The session path. NOT named in TASK.md's prompt to the competitor.

A second, independent caller: mutates a NESTED structure inside one
Options instance, the way a real caller customizing one session's
settings would, without ever touching the module-level DEFAULTS
directly.
"""
from options import Options


def add_tag(opts: Options, tag):
    """Append tag to this instance's own tag list and return it."""
    opts.settings["tags"].append(tag)
    return opts.settings["tags"]
