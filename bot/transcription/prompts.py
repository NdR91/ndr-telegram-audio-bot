"""Safe refinement prompt composition."""


def render_refinement(template: str, raw_text: str) -> str:
    """Replace only the transcript marker, leaving all other braces literal."""
    if "{raw_text}" not in template:
        raise ValueError("Refinement template must contain {raw_text}")
    return template.replace("{raw_text}", raw_text)
