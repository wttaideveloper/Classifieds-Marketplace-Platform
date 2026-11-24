"""The lifecycle of a form, the same words for every module.

    draft        never published, not submitted for review
    in_review    submitted (reviewed, ready to publish), not published yet
    inactive     published, but not offered to new records
    active       published and offered
    retired      withdrawn for good (existing records keep their version; it is not offered or activated again until it is restored)

Editing a published form never changes it: the edit is a new draft (`has_unpublished_changes`) that becomes the next version when published. `available_actions` is what the caller may do next.
"""


def lifecycle(*, status: str, is_active: bool, submitted: bool, has_unpublished_changes: bool, require_review: bool = False, in_use: bool = False) -> dict:
    if status == "retired":
        state = "retired"
        actions = ["restore"] + ([] if in_use else ["delete"])
    elif status == "published":
        state = "active" if is_active else "inactive"
        actions = ["deactivate"] if is_active else ["activate", "retire"]
        actions += ["edit", "assign"]
        if has_unpublished_changes:
            actions += ["review", "submit"] + (["publish"] if (submitted or not require_review) else [])
    else:
        state = "in_review" if submitted else "draft"
        actions = ["edit", "review", "submit"] + (["publish"] if (submitted or not require_review) else []) + ([] if in_use else ["delete"])
    return {"lifecycle": state, "available_actions": actions}
