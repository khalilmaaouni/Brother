"""Brother Core product identity information."""


def product_identity():
    """Return the Brother product identity as a dictionary.

    Returns:
        dict: Product metadata including name, version, author, license,
              repository, and description.
    """
    return {
        "name": "brother",
        "displayName": "Brother",
        "version": "1.1.0-rc.1",
        "author": "Khalil Maaouni",
        "license": "MIT",
        "repository": "https://github.com/khalilmaaouni/Brother",
        "description": "Brother is an assurance and execution layer for delegated AI engineering.",
    }
