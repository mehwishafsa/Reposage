def slugify(text):
    """Make a URL-friendly slug."""
    return _clean(text).replace(" ", "-")


def _clean(text):
    return text.strip().lower()
