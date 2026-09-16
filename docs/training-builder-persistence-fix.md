# Training builder persistence investigation

## Confirmed in repository code

Both the section title reverting and lesson media removals reverting were reproduced with already-normalized stored curricula, using real SQLAlchemy sessions and a SQLite JSON test database.

`save_builder_curriculum` already called `db.commit()`. The problem was dirty tracking: route handlers modified nested dictionaries in the stored `sections` JSON in place. SQLAlchemy's mutable list does not track nested dictionary mutations. Reassigning an equal normalized list could therefore produce no sections UPDATE.

- With `expire_on_commit=True`, reading the response after commit reloaded the old title/media.
- With `expire_on_commit=False`, the response could show the new value while a fresh session still read the old database value.
- `DELETE .../lessons/{lesson_id}/media?kind=videos&url=...` uses the same helper and had the same failure. It did call commit, but the JSON mutation could remain untracked.

The helper now explicitly marks `sections`, `assessments`, and `assignments` as modified, commits, and refreshes the Training before building the response. Explicit empty lists remain empty after fresh reads. No migration is required for this fix.

## Question-creation 500: local reproduction

An assessment stored with `"questions": null` caused:

```text
app/services/training_service.py, add_assessment_question_service
    qs.append(new_q)
AttributeError: 'NoneType' object has no attribute 'append'
```

This happened with both omitted options and the reported `[{"id":"a","label":"..."}]` options shape. `dict.get("questions", [])` does not substitute the default when the key exists with a null value.

The route now treats missing/null questions as an empty list, preserves existing lists, and returns HTTP 400 for an invalid stored non-array shape. It serializes the new question as JSON, commits, refreshes, and returns the persisted question. This is a reproduced cause, not proof that the deployed assessment has null questions.

## Deployment verification is still outstanding

The server/container connection and production log source are not available in this task. Local repository HEAD at investigation was `6d70061`; these fixes are working-tree changes, not evidence of a deployed release. No claim is made that the deployed code matches this checkout.

The application's global exception handler already records method, URL path, and full traceback with `logger.exception`; it deliberately returns a generic HTTP 500 body to clients.

For the documented Docker deployment, an operator can inspect:

```sh
docker logs --since 30m --timestamps marketplace-api
docker inspect --format '{{.Image}}' marketplace-api
docker image inspect --format '{{json .Config.Labels}}' IMAGE_ID
```

Use the actual container name if different. Match the request timestamp and `/api/v1/trainings/.../assessments/.../questions` path. Image labels may identify the revision only if CI populated them; a source checkout's `git rev-parse HEAD` alone does not prove what a running container contains.

Relevant regression tests: `tests/test_training_builder_persistence.py`. They check returned values and fresh-session reads with both commit-expiration settings. These tests exercise real ORM persistence but do not substitute for a deployed PostgreSQL smoke test.
