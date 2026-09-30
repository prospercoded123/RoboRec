"""Title/subtitle for a recovery run that did not find a phrase, shared by every panel.

A run that ends without a hit is one of three different things, and the user must be able to
tell them apart — "no match" means the whole search space was checked and nothing matched, which
is actionable advice (re-check words/address); an engine failure means the search never finished,
so telling the user to re-check their input would send them in the wrong direction.
"""

from __future__ import annotations

from robo_rec.recovery.models import RecoveryResult

ERROR_TITLE = "The search didn't finish"
CANCELLED_TITLE = "Search cancelled"
CANCELLED_SUBTITLE = "You stopped the search before it finished, so no result was found."


def failure_text(
    result: RecoveryResult, *, not_found_title: str, not_found_subtitle: str
) -> tuple[str, str]:
    """(title, subtitle) for a non-successful result. Only a completed search gets the
    panel's own "not found" wording."""
    if result.error:
        return ERROR_TITLE, (
            f"{result.error}\n\nThis is not the same as \"no match\": the search stopped early, "
            "so your words and address haven't been ruled out."
        )
    if result.cancelled:
        return CANCELLED_TITLE, CANCELLED_SUBTITLE
    return not_found_title, not_found_subtitle
