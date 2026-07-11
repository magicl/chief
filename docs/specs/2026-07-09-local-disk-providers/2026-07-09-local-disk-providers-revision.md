# Local disk providers — Implementation Review

> For the reviewer. Created before implementation; fill in after reviewing the completed work.
> Implementers follow `-plan.md` only — do not read this file unless the user asks.

## Review notes

<!-- Corrections, gaps, and follow-ups discovered while reviewing the implementation. -->

## Items to address

- [x] local_disk should not be a separate app.. rather probably put them under lib.. maybe we have lib/providers/llm/... for llms, lib/providers/key/... for key providers, lib/providers/data for data providers for now..
- [x] we should probably separate the concepts of key providers and "data providers", where data providers can provide agents and other resources (later we'll add static data sources here as well)
- [x] general file utils should be extracted into lib/file/... or something .. i.e. for hashing etc.
- [x] also, let's fix all the review fixes in the review.md file associated with the spec.
